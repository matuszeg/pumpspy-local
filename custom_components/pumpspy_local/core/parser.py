"""Turn the device's wire format into readings.

Shape notes, all from real captures:

- ``POST /bbs_json`` is an object whose ``json`` key holds a *string* of escaped
  JSON. That inner object carries only what changed, not a full state snapshot,
  so every field on :class:`BbsReading` is optional.
- ``POST /pings`` and ``POST /pump_outlet_alerts`` are JSON *arrays*.
- ``/pump_outlet_alerts`` uses a camelCase schema of its own.
- ``GET /tm`` has no body worth reading in either direction here: it is the
  device asking the vendor for the time, answered ``{"utctime": <ms>}``. That
  is why its own timestamps sit within a second of ours.
- Voltages are millivolts on the wire, and run durations are tenths of a second.
- Every message carries ``utcunixtime``, the device's own clock in milliseconds.
  It is the only timestamp that is not ours: everything else is arrival time.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PumpRun:
    """A single pump cycle reported by the device."""

    pump: str
    duration_seconds: float
    # None when the device wrote a current it could not format as a number.
    current_milliamps: int | None


@dataclass(frozen=True)
class BbsReading:
    """One ``/bbs_json`` message. Absent fields stay ``None``."""

    device_id: str
    # When the *device* says this message was made, as distinct from when it
    # reached us. The two normally differ by a steady offset -- device clocks
    # drift -- so the value is only meaningful compared against that baseline.
    # A message whose offset jumps is one that waited somewhere.
    device_time: datetime | None = None
    battery_volts: float | None = None
    loaded_volts: float | None = None
    ac_power: bool | None = None
    high_water: bool | None = None
    motor_fail: bool | None = None
    pump_run: PumpRun | None = None
    # Fields the device sent that this parser does not know. Carried rather
    # than logged here so that one thing decides what is worth saying about an
    # unfamiliar message, and says it once. See ``core/novelty.py``.
    unknown_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ping:
    """One ``/pings`` entry.

    ``data_type`` 1 is Wi-Fi RSSI in dBm. Type 3 is motor current in amps: a
    capture of a hose-fed backup run reported ``5.860000`` here beside
    ``"mamp": 5856`` in the run message, the same figure in different units. It
    is event-driven rather than periodic, arriving alongside pump activity,
    where RSSI arrives every two minutes come what may.
    """

    device_id: str
    data_type: int
    value: float


@dataclass(frozen=True)
class PumpAlert:
    """One ``/pump_outlet_alerts`` entry.

    This endpoint spells things in camelCase (``deviceID``, ``utcunixTime``)
    unlike the rest of the protocol, which suggests a newer subsystem.

    It is not a pump event. All twelve occurrences in fourteen days of the
    shim's access log land inside the device's reconnect burst, in the same
    second as ``/tm``, ``/oauth/token`` and ``/bbs_parameters``. The one
    captured body carries ``idPumpAlertType: 105``, ``recordNumber: 0`` and
    ``value: 0``, and the vendor answers ``{"numRows":-1}``, so it reads as
    "nothing to report" for an outlet subsystem this hardware may not have.
    """

    device_id: str
    alert_type: int
    record_number: int
    value: float


ParsedMessage = BbsReading | list[Ping] | list[PumpAlert]

# Anything outside this set is new, and new almost certainly means the firmware
# changed — worth surfacing rather than swallowing.
_KNOWN_BBS_FIELDS = frozenset(
    {
        "battery_voltage",
        "loaded",
        "ac_power",
        "high_water",
        "motor_fail",
        "motor",
        "time",
        "mamp",
    }
)


def _device_time(milliseconds: object) -> datetime | None:
    """Read ``utcunixtime``, or ``None`` if it is missing or unreadable.

    Never allowed to fail the parse. The readings in the same message are worth
    more than the timestamp, and a firmware that changed this field must not
    cost us the battery voltage beside it.
    """
    if milliseconds is None:
        return None
    try:
        return datetime.fromtimestamp(float(milliseconds) / 1000, timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        _LOGGER.debug("unreadable utcunixtime %r", milliseconds)
        return None


def _volts(millivolts: int | None) -> float | None:
    return None if millivolts is None else millivolts / 1000


def _flag(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _pump_run(inner: dict) -> PumpRun | None:
    if "motor" not in inner:
        return None
    return PumpRun(
        pump="primary" if inner["motor"] == 1 else "backup",
        # `time` is tenths of a second: 82 means 8.2s, not 82s.
        duration_seconds=inner["time"] / 10,
        current_milliamps=inner["mamp"],
    )


def parse_bbs_json(raw: bytes) -> BbsReading:
    """Parse a ``/bbs_json`` body."""
    outer = json.loads(raw)
    inner = json.loads(outer["json"])

    return BbsReading(
        device_id=str(outer["deviceid"]),
        device_time=_device_time(outer.get("utcunixtime")),
        battery_volts=_volts(inner.get("battery_voltage")),
        loaded_volts=_volts(inner.get("loaded")),
        ac_power=_flag(inner.get("ac_power")),
        high_water=_flag(inner.get("high_water")),
        motor_fail=_flag(inner.get("motor_fail")),
        pump_run=_pump_run(inner),
        unknown_fields=tuple(sorted(set(inner) - _KNOWN_BBS_FIELDS)),
    )


def parse_pings(raw: bytes) -> list[Ping]:
    """Parse a ``/pings`` body, which is an array of entries."""
    return [
        Ping(
            device_id=str(entry["deviceid"]),
            data_type=entry["idpings_data_type"],
            value=float(entry["value"]),
        )
        for entry in json.loads(raw)
    ]


def parse_pump_alerts(raw: bytes) -> list[PumpAlert]:
    """Parse a ``/pump_outlet_alerts`` body, which is an array of entries."""
    return [
        PumpAlert(
            device_id=str(entry["deviceID"]),
            alert_type=entry["idPumpAlertType"],
            record_number=entry["recordNumber"],
            value=float(entry["value"]),
        )
        for entry in json.loads(raw)
    ]


# What a C ``printf("%f")`` writes when it is handed a value that is not finite:
# ``1.#INF00``, ``-1.#IND00``, ``1.#QNAN0``. The device does this to
# ``utcunixtime`` when its clock is invalid, and the result is not JSON, so the
# decoder abandons the entire message rather than the one bad field. Every parse
# failure ever recorded on the live install was this, byte for byte -- 892 of
# them, all reporting the same column -- and for a /pings body it costs us the
# signal strength, which is the only thing in there we read at all.
#
# Matching on the raw bytes reaches the second JSON document /bbs_json hides
# inside a string, because none of these characters need escaping and so they
# appear there verbatim. A digit followed by ``.#`` does not occur anywhere in
# this protocol's real values, and the substitution only ever runs on a body
# that has already failed to parse.
_NON_FINITE = re.compile(rb"-?\d+\.#[A-Za-z]+\d*")

_PARSERS = {
    "/bbs_json": parse_bbs_json,
    "/pings": parse_pings,
    "/pump_outlet_alerts": parse_pump_alerts,
}


# The endpoints this module reads, for anything that needs to tell "no parser"
# apart from "the parser failed".
PARSER_PATHS = frozenset(_PARSERS)


def parse_request(path: str, body: bytes) -> ParsedMessage | None:
    """Parse a device request, or return ``None`` if it cannot be parsed.

    This is the boundary the request handler calls, and it never raises.
    Parsing runs alongside forwarding the device's data to the vendor, so a
    parse failure must never cost the device its delivery.
    """
    parser = _PARSERS.get(path.split("?")[0])
    if parser is None:
        _LOGGER.debug("no parser for path %s", path)
        return None

    try:
        return parser(body)
    except Exception as err:
        repaired = _NON_FINITE.sub(b"null", body)
        if repaired != body:
            try:
                parsed = parser(repaired)
            except Exception:
                # The non-finite value was not the only thing wrong with it.
                # Fall through and report the original failure, not this one.
                pass
            else:
                _LOGGER.debug(
                    "%s carried a non-finite number, read without it", path
                )
                return parsed
        _LOGGER.warning("could not parse %s body", path, exc_info=err)
        return None
