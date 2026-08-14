"""Shared entity wiring: identity, and refreshing when the device reports."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, MANUFACTURER, MODEL, signal_device_update
from .core.state import DeviceState


class PumpspyEntity(Entity):
    """Base for everything this integration creates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: DeviceState, description: EntityDescription) -> None:
        self._device = device
        self.entity_description = description
        self._attr_unique_id = f"{device.device_id}_{description.key}"
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
