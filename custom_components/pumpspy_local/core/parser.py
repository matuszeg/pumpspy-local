"""Turn the device's wire format into readings.

Shape notes, from real captures:

- ``POST /bbs_json`` is an object whose ``json`` key holds a *string* of escaped
  JSON. That inner object carries only the field or two that changed, not a full
  state snapshot, so every field here is optional.
- Voltages are millivolts on the wire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


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
        pump_run=_pump_run(inner),
        battery_volts=_volts(inner.get("battery_voltage")),
        loaded_volts=_volts(inner.get("loaded")),
        ac_power=_flag(inner.get("ac_power")),
        high_water=_flag(inner.get("high_water")),
        motor_fail=_flag(inner.get("motor_fail")),
    )
