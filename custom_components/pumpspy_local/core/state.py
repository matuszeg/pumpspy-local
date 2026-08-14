"""Accumulated per-device state.

The device reports only what changed — a mains-power message is literally
``{"ac_power": 0}`` and carries nothing else. Entities need a full picture, so
this layer merges each message into the last known one. A field that is absent
from a message means "unchanged", never "zero".
"""

from __future__ import annotations

from dataclasses import dataclass

from .parser import BbsReading, Ping, PumpRun

# ``idpings_data_type`` 1 is Wi-Fi RSSI. Type 3 has been seen (~5.86) but nobody
# has confirmed what it means, so it is deliberately not mapped to anything.
PING_WIFI_RSSI = 1


@dataclass
class DeviceState:
    """Everything currently known about one device."""

    device_id: str
    battery_volts: float | None = None
    loaded_volts: float | None = None
    ac_power: bool | None = None
    high_water: bool | None = None
    motor_fail: bool | None = None
    last_run: PumpRun | None = None
    wifi_dbm: float | None = None

    # A run reported before the event entity existed -- which happens whenever a
    # device's very first message is a pump run, since the entity is created in
    # response to that same message. The entity fires and clears it once it is
    # listening. In memory only, so a restart cannot replay a stale run.
    unfired_run: PumpRun | None = None

    def apply(self, reading: BbsReading) -> None:
        """Merge a reading in, leaving fields it does not mention alone."""
        for field in (
            "battery_volts",
            "loaded_volts",
            "ac_power",
            "high_water",
            "motor_fail",
        ):
            value = getattr(reading, field)
            if value is not None:
                setattr(self, field, value)

        if reading.pump_run is not None:
            self.last_run = reading.pump_run

    def apply_ping(self, ping: Ping) -> None:
        """Merge a ping in. Unrecognised types are left alone, not guessed at."""
        if ping.data_type == PING_WIFI_RSSI:
            self.wifi_dbm = ping.value
