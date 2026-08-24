"""Sensor entities.

Entities are created when a device first reports, not from configuration — the
device id arrives in the telemetry, so there is nothing to ask the user for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW_DEVICE
from .core.state import DeviceState
from .entity import PumpspyEntity


@dataclass(frozen=True, kw_only=True)
class PumpspySensorDescription(SensorEntityDescription):
    """A sensor and how to read it out of device state."""

    value_fn: Callable[[DeviceState], float | str | datetime | None]


SENSORS: tuple[PumpspySensorDescription, ...] = (
    PumpspySensorDescription(
        key="battery_voltage",
        name="Battery voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.battery_volts,
    ),
    PumpspySensorDescription(
        key="battery_voltage_under_load",
        name="Battery voltage under load",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        # Only reported alongside a pump run, so this updates on runs only.
        # That is the reading that actually reveals a dying battery.
        value_fn=lambda device: device.loaded_volts,
    ),
    PumpspySensorDescription(
        key="wifi_signal",
        # "Wi-Fi signal" would slugify to wi_fi_signal, which is awkward to type
        # in an automation.
        name="WiFi signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.wifi_dbm,
    ),
    PumpspySensorDescription(
        key="last_pump",
        name="Last pump",
        device_class=SensorDeviceClass.ENUM,
        options=["primary", "backup"],
        value_fn=lambda device: device.last_run.pump if device.last_run else None,
    ),
    PumpspySensorDescription(
        key="last_run_duration",
        name="Last run duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda device: (
            device.last_run.duration_seconds if device.last_run else None
        ),
    ),
    PumpspySensorDescription(
        key="last_run_current",
        name="Last run current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE,
        value_fn=lambda device: (
            device.last_run.current_milliamps if device.last_run else None
        ),
    ),
)


def _totals_sensors() -> tuple[PumpspySensorDescription, ...]:
    """Run and gallon counters, per pump.

    Kept separate per pump because a backup run means the mains failed; folding
    both into one figure would hide the thing most worth noticing.
    """
    sensors: list[PumpspySensorDescription] = []
    for pump in ("primary", "backup"):
        label = pump.capitalize()
        sensors.extend(
            (
                PumpspySensorDescription(
                    key=f"{pump}_runs_today",
                    name=f"{label} runs today",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device, p=pump: device.totals[p].runs_today,
                ),
                PumpspySensorDescription(
                    key=f"{pump}_estimated_gallons_today",
                    name=f"{label} estimated gallons today",
                    native_unit_of_measurement="gal",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device, p=pump: device.totals[p].gallons_today,
                ),
                PumpspySensorDescription(
                    key=f"{pump}_runs_total",
                    name=f"{label} runs total",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device, p=pump: device.totals[p].runs_total,
                ),
                PumpspySensorDescription(
                    key=f"{pump}_estimated_gallons_total",
                    name=f"{label} estimated gallons total",
                    native_unit_of_measurement="gal",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device, p=pump: device.totals[p].gallons_total,
                ),
            )
        )
    return tuple(sensors)


SENSORS = SENSORS + (
    # The controller load-tests the backup battery for us at least three times a
    # week. Those runs are kept out of the backup counters -- they move little
    # or no water -- but the measurement is too good to throw away: resting
    # voltage stays healthy on a battery that is nearly dead, and this is the
    # only regular look at what it does under real load.
    PumpspySensorDescription(
        key="last_self_test",
        name="Last self test",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: (
            device.last_self_test.at if device.last_self_test else None
        ),
    ),
    PumpspySensorDescription(
        key="last_self_test_voltage",
        # The number that actually reveals a dying battery: how far the pack
        # sagged while the backup pump was pulling current.
        name="Last self test voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: (
            device.last_self_test.loaded_volts if device.last_self_test else None
        ),
    ),
    PumpspySensorDescription(
        key="device_clock_offset",
        # Diagnostic, and honestly named: this is not the device's clock error,
        # it is that plus however long the message took to arrive. The two
        # cannot be separated from here, and for the question it exists to
        # answer -- was this message made now, or made earlier and held -- they
        # do not need to be.
        name="Device clock offset",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.clock_offset_seconds,
    ),
    PumpspySensorDescription(
        key="last_run_estimated_gallons",
        # "Estimated" in the name on purpose: this is derived from run duration,
        # not measured, and matches how the vendor's own app frames it.
        name="Last run estimated gallons",
        native_unit_of_measurement="gal",
        value_fn=lambda device: device.last_run_gallons,
    ),
) + _totals_sensors()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors, now and as devices appear."""
    runtime = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add(device: DeviceState) -> None:
        async_add_entities(
            PumpspySensor(device, description, entry.entry_id)
            for description in SENSORS
        )

    # A device may already have reported before this platform finished loading.
    for device in runtime.devices.values():
        _add(device)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add))


class PumpspySensor(PumpspyEntity, SensorEntity):
    """A reading pulled out of device state."""

    entity_description: PumpspySensorDescription

    @property
    def native_value(self) -> float | str | None:
        return self.entity_description.value_fn(self._device)
