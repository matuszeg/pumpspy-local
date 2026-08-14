"""Turn the device's wire format into readings.

Shape notes, all from real captures:

- ``POST /bbs_json`` is an object whose ``json`` key holds a *string* of escaped
  JSON. That inner object carries only what changed, not a full state snapshot,
  so every field on :class:`BbsReading` is optional.
- ``POST /pings`` and ``POST /pump_outlet_alerts`` are JSON *arrays*.
- ``/pump_outlet_alerts`` uses a camelCase schema of its own.
- Voltages are millivolts on the wire, and run durations are tenths of a second.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PumpRun:
    """A single pump cycle reported by the device."""

    pump: str
    duration_seconds: float
    current_milliamps: int


@dataclass(frozen=True)
class BbsReading:
    """One ``/bbs_json`` message. Absent fields stay ``None``."""

    device_id: str
    battery_volts: float | None = None
    loaded_volts: float | None = None
    ac_power: bool | None = None
    high_water: bool | None = None
    motor_fail: bool | None = None
    pump_run: PumpRun | None = None


@dataclass(frozen=True)
class Ping:
    """One ``/pings`` entry.

    ``data_type`` 1 is Wi-Fi RSSI in dBm. Type 3 has been observed (~5.86) but
    its meaning is unconfirmed, so the value is carried through uninterpreted
    rather than guessed at.
    """

    device_id: str
    data_type: int
    value: float


@dataclass(frozen=True)
class PumpAlert:
    """One ``/pump_outlet_alerts`` entry.

    This endpoint spells things in camelCase (``deviceID``, ``utcunixTime``)
    unlike the rest of the protocol, which suggests a newer subsystem. The
    meaning of ``alert_type`` is not yet known.
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

    unknown = set(inner) - _KNOWN_BBS_FIELDS
    if unknown:
        _LOGGER.info("unknown /bbs_json fields %s", sorted(unknown))

    return BbsReading(
        device_id=str(outer["deviceid"]),
        battery_volts=_volts(inner.get("battery_voltage")),
        loaded_volts=_volts(inner.get("loaded")),
        ac_power=_flag(inner.get("ac_power")),
        high_water=_flag(inner.get("high_water")),
        motor_fail=_flag(inner.get("motor_fail")),
        pump_run=_pump_run(inner),
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


_PARSERS = {
    "/bbs_json": parse_bbs_json,
    "/pings": parse_pings,
    "/pump_outlet_alerts": parse_pump_alerts,
}


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
    except Exception:
        _LOGGER.warning("could not parse %s body", path, exc_info=True)
        return None
