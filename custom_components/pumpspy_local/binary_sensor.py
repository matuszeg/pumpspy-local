"""Binary sensor entities: the alarms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW_DEVICE
from .core.state import DeviceState
from .entity import PumpspyEntity


@dataclass(frozen=True, kw_only=True)
class PumpspyBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor and how to read it out of device state."""

    value_fn: Callable[[DeviceState], bool | None]


BINARY_SENSORS: tuple[PumpspyBinarySensorDescription, ...] = (
    PumpspyBinarySensorDescription(
        key="mains_power",
        name="Mains power",
        device_class=BinarySensorDeviceClass.POWER,
        # ac_power 1 means mains present, so "off" is the alarming state here.
        value_fn=lambda device: device.ac_power,
    ),
    PumpspyBinarySensorDescription(
        key="high_water",
        name="High water",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=lambda device: device.high_water,
    ),
    PumpspyBinarySensorDescription(
        key="pump_failure",
        name="Pump failure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # The device latches this and never sends 0; clearing it is issue #5.
        value_fn=lambda device: device.motor_fail,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors, now and as devices appear."""
    runtime = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add(device: DeviceState) -> None:
        async_add_entities(
            PumpspyBinarySensor(device, description)
            for description in BINARY_SENSORS
        )

    for device in runtime.devices.values():
        _add(device)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add))


class PumpspyBinarySensor(PumpspyEntity, BinarySensorEntity):
    """An alarm pulled out of device state."""

    entity_description: PumpspyBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._device)
