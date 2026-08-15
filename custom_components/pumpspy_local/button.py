"""Buttons.

The fault latch clears automatically on a healthy primary run, but that rule
depends on a current threshold nobody has calibrated against real hardware. This
is the unambiguous way out that does not involve waiting for the pump to run.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW_DEVICE, signal_device_update
from .core.state import DeviceState
from .entity import PumpspyEntity

DESCRIPTION = ButtonEntityDescription(
    key="clear_pump_failure",
    name="Clear pump failure",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the clear button, now and as devices appear."""
    runtime = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add(device: DeviceState) -> None:
        async_add_entities([ClearPumpFailure(device, DESCRIPTION, runtime)])

    for device in runtime.devices.values():
        _add(device)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add))


class ClearPumpFailure(PumpspyEntity, ButtonEntity):
    """Clears the latched pump fault."""

    def __init__(self, device: DeviceState, description, runtime) -> None:
        super().__init__(device, description)
        self._runtime = runtime

    async def async_press(self) -> None:
        self._device.clear_fault()
        # Persist immediately: a cleared fault that came back after a restart
        # would be worse than not offering the button at all.
        self._runtime.request_save()
        async_dispatcher_send(
            self.hass, signal_device_update(self._device.device_id)
        )
