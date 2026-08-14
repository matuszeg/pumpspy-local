"""Sensor entities.

Entities are created when a device first reports, not from configuration —
the device id arrives in the telemetry, so there is nothing to ask the user for.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricPotential
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MODEL, SIGNAL_NEW_DEVICE, signal_device_update
from .core.state import DeviceState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors, now and as devices appear."""
    runtime = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add(device: DeviceState) -> None:
        async_add_entities([BatteryVoltage(device)])

    # A device may already have reported before this platform finished loading.
    for device in runtime.devices.values():
        _add(device)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add))


class PumpspyEntity(SensorEntity):
    """Shared wiring: identity, and refreshing when the device reports."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: DeviceState, key: str) -> None:
        self._device = device
        self._attr_unique_id = f"{device.device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=f"PumpSpy {device.device_id}",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_device_update(self._device.device_id),
                self.async_write_ha_state,
            )
        )


class BatteryVoltage(PumpspyEntity):
    """Resting battery voltage."""

    _attr_name = "Battery voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: DeviceState) -> None:
        super().__init__(device, "battery_voltage")

    @property
    def native_value(self) -> float | None:
        return self._device.battery_volts
