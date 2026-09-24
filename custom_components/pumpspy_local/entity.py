"""Shared entity wiring: identity, and refreshing when the device reports."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, MANUFACTURER, MODEL, signal_device_update
from .core.state import DeviceState


# Home Assistant 2026.9 deprecated naming the parent device by its identifiers
# (via_device) in favour of its registry id (via_device_id), and the old form
# stops working in 2027.8. Older releases only have the old form, so ask the
# running version which one it knows rather than pinning a minimum.
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


class PumpspyEntity(Entity):
    """Base for everything this integration creates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        device: DeviceState,
        description: EntityDescription,
        entry_id: str,
    ) -> None:
        self._device = device
        self.entity_description = description
        self._attr_unique_id = f"{device.device_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=f"PumpSpy {device.device_id}",
        )
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        """The pump, hung off the service device the config entry owns.

        Without the link the pump and the integration show up as two
        unrelated devices. Resolved here rather than in __init__ because the
        registry id lives on the runtime, which needs hass, and Home Assistant
        sets that before it reads this. Setup records the id before any
        platform starts, so it is there. Looking the device up in the registry
        instead is itself deprecated from 2026.9.
        """
        info = DeviceInfo(**self._attr_device_info)
        runtime = self.hass.data[DOMAIN][self._entry_id]
        if _HAS_VIA_DEVICE_ID and runtime.service_device_id:
            info["via_device_id"] = runtime.service_device_id
        else:
            info["via_device"] = (DOMAIN, self._entry_id)
        return info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_device_update(self._device.device_id),
                self.async_write_ha_state,
            )
        )
