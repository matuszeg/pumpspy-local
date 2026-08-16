"""Accumulated per-device state.

The device reports only what changed — a mains-power message is literally
``{"ac_power": 0}`` and carries nothing else. Entities need a full picture, so
this layer merges each message into the last known one. A field that is absent
from a message means "unchanged", never "zero".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .gallons import DEFAULT_FLOW_RATE, estimated_gallons
from .parser import BbsReading, Ping, PumpRun

BACKUP_PUMP = "backup"

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
class PumpTotals:
    """Run and gallon counts for one pump."""

    runs_today: int = 0
    gallons_today: int = 0
    runs_total: int = 0
    gallons_total: int = 0

    def add(self, gallons: int) -> None:
        self.runs_today += 1
        self.gallons_today += gallons
        self.runs_total += 1
        self.gallons_total += gallons

    def reset_daily(self) -> None:
        self.runs_today = 0
        self.gallons_today = 0


def _fresh_totals() -> dict[str, PumpTotals]:
    # Counted per pump on purpose: backup runs mean the mains failed, and folding
    # them into one figure would hide exactly the thing worth noticing.
    return {PRIMARY_PUMP: PumpTotals(), BACKUP_PUMP: PumpTotals()}


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

    last_run_gallons: int | None = None
    totals: dict[str, PumpTotals] = field(default_factory=_fresh_totals)
    totals_date: date | None = None
    flow_rate: float = DEFAULT_FLOW_RATE

    def apply(self, reading: BbsReading, today: date | None = None) -> None:
        """Merge a reading in, leaving fields it does not mention alone.

        ``today`` is passed in rather than read from the clock so the daily
        rollover is testable; it defaults to the local date.
        """
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
            self._count_run(reading.pump_run, today or date.today())
            if self._is_healthy_primary_run(reading.pump_run):
                self.motor_fail = False

    def _count_run(self, run: PumpRun, today: date) -> None:
        gallons = estimated_gallons(run.duration_seconds, self.flow_rate)
        self.last_run_gallons = gallons

        if self.totals_date != today:
            for totals in self.totals.values():
                totals.reset_daily()
            self.totals_date = today

        self.totals[run.pump].add(gallons)

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

        The test is whether the device reports it *continuously* or only when
        something happens. Resting voltage and signal strength arrive every
        couple of minutes, so restoring them would paint a stale number as
        current for no gain -- they refresh immediately anyway.

        ``loaded_volts`` looks like a voltage but belongs with the events. It
        is only ever sent alongside a pump run, so it is not resent within a
        cycle; it is resent on the next run, which on a pit that stays dry can
        be weeks away. The device treats it as retained itself -- captures show
        it repeating the last measured figure on subsequent runs rather than
        remeasuring. Dropping it discards the one reading that reveals a dying
        battery, silently, and leaves the entity reading unknown as though
        nothing had ever happened.
        """
        return {
            "motor_fail": self.motor_fail,
            "ac_power": self.ac_power,
            "high_water": self.high_water,
            "loaded_volts": self.loaded_volts,
            "last_run": (
                {
                    "pump": self.last_run.pump,
                    "duration_seconds": self.last_run.duration_seconds,
                    "current_milliamps": self.last_run.current_milliamps,
                }
                if self.last_run is not None
                else None
            ),
            "last_run_gallons": self.last_run_gallons,
            "totals_date": self.totals_date.isoformat() if self.totals_date else None,
            "totals": {
                pump: {
                    "runs_today": totals.runs_today,
                    "gallons_today": totals.gallons_today,
                    "runs_total": totals.runs_total,
                    "gallons_total": totals.gallons_total,
                }
                for pump, totals in self.totals.items()
            },
        }

    @classmethod
    def from_stored(cls, device_id: str, stored: dict) -> DeviceState:
        """Rebuild from a stored payload, tolerating one written by an older version."""
        run = stored.get("last_run")
        totals = _fresh_totals()
        for pump, counts in (stored.get("totals") or {}).items():
            if pump in totals:
                totals[pump] = PumpTotals(**counts)

        totals_date = stored.get("totals_date")
        return cls(
            device_id=device_id,
            motor_fail=stored.get("motor_fail"),
            ac_power=stored.get("ac_power"),
            high_water=stored.get("high_water"),
            loaded_volts=stored.get("loaded_volts"),
            last_run=PumpRun(**run) if run else None,
            last_run_gallons=stored.get("last_run_gallons"),
            totals=totals,
            totals_date=date.fromisoformat(totals_date) if totals_date else None,
        )

    def apply_ping(self, ping: Ping) -> None:
        """Merge a ping in. Unrecognised types are left alone, not guessed at."""
        if ping.data_type == PING_WIFI_RSSI:
            self.wifi_dbm = ping.value
