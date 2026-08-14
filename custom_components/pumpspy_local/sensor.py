"""Sensor entities.

Entities are created when a device first reports, not from configuration — the
device id arrives in the telemetry, so there is nothing to ask the user for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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

    value_fn: Callable[[DeviceState], float | str | None]


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
            PumpspySensor(device, description) for description in SENSORS
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
