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

# The device reports motor_fail once and never sends a clearing message; the
# vendor clears it server-side when a healthy primary run comes in. We reproduce
# that locally.
#
# "Healthy" needs a current threshold, and there is no calibrated figure from the
# captures -- the one real run observed drew 2800 mA. The default is deliberately
# permissive (any current at all), because wrongly clearing a real fault is the
# expensive mistake and a manual clear button exists for the other direction.
DEFAULT_HEALTHY_RUN_MILLIAMPS = 1

PRIMARY_PUMP = "primary"


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

    # Minimum current for a primary run to count as evidence the pump works.
    healthy_run_milliamps: int = DEFAULT_HEALTHY_RUN_MILLIAMPS

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
            if self._is_healthy_primary_run(reading.pump_run):
                self.motor_fail = False

    def _is_healthy_primary_run(self, run: PumpRun) -> bool:
        """Whether a run is evidence the primary pump is working.

        A backup run says nothing about the primary -- if anything it suggests
        the primary is not doing its job -- and a run drawing no current is the
        failure itself rather than evidence against it.
        """
        return (
            run.pump == PRIMARY_PUMP
            and run.current_milliamps >= self.healthy_run_milliamps
        )

    def clear_fault(self) -> None:
        """Clear the latched fault by hand."""
        self.motor_fail = False

    def to_stored(self) -> dict:
        """The part of this state worth surviving a restart.

        Only what the device reports *on change*. Voltages and signal strength
        arrive on their own schedule and will be resent within a cycle, so
        restoring them would just show a stale number as though it were current.
        """
        return {
            "motor_fail": self.motor_fail,
            "ac_power": self.ac_power,
            "high_water": self.high_water,
            "last_run": (
                {
                    "pump": self.last_run.pump,
                    "duration_seconds": self.last_run.duration_seconds,
                    "current_milliamps": self.last_run.current_milliamps,
                }
                if self.last_run is not None
                else None
            ),
        }

    @classmethod
    def from_stored(cls, device_id: str, stored: dict) -> DeviceState:
        """Rebuild from a stored payload, tolerating one written by an older version."""
        run = stored.get("last_run")
        return cls(
            device_id=device_id,
            motor_fail=stored.get("motor_fail"),
            ac_power=stored.get("ac_power"),
            high_water=stored.get("high_water"),
            last_run=PumpRun(**run) if run else None,
        )

    def apply_ping(self, ping: Ping) -> None:
        """Merge a ping in. Unrecognised types are left alone, not guessed at."""
        if ping.data_type == PING_WIFI_RSSI:
            self.wifi_dbm = ping.value
