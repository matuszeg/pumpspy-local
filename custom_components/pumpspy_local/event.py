"""Pump runs, as events.

A run is something that happened, not a value that is true. Modelling it as a
sensor would force an automation to watch for a number changing and infer a run
from it, which misses back-to-back runs of identical length.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW_DEVICE, signal_pump_run
from .core.parser import PumpRun
from .core.state import DeviceState
from .entity import PumpspyEntity

DESCRIPTION = EventEntityDescription(key="pump_run", name="Pump run")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the pump run event, now and as devices appear."""
    runtime = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add(device: DeviceState) -> None:
        async_add_entities([PumpRunEvent(device, DESCRIPTION, entry.entry_id)])

    for device in runtime.devices.values():
        _add(device)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add))


class PumpRunEvent(PumpspyEntity, EventEntity):
    """Fires whenever the device reports that a pump ran."""

    _attr_event_types = ["primary", "backup"]

    async def async_added_to_hass(self) -> None:
        # Deliberately not the base class's state-update subscription: this
        # entity reacts to runs alone, not to every message the device sends.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_pump_run(self._device.device_id),
                self._handle_run,
            )
        )

        # A run that arrived before this entity existed. Happens when a device's
        # first ever message is a pump run, because that message is what creates
        # the entity in the first place.
        if self._device.unfired_run is not None:
            self._handle_run(self._device.unfired_run)

    @callback
    def _handle_run(self, run: PumpRun) -> None:
        self._device.unfired_run = None
        self._trigger_event(
            run.pump,
            {
                "duration_seconds": run.duration_seconds,
                "current_milliamps": run.current_milliamps,
                # How far behind arrival the device's own clock was on the
                # message that carried this run. Read against the device's
                # usual offset, not on its own: a figure far from the usual one
                # means the run was reported late, not that it just happened.
                "clock_offset_seconds": self._device.clock_offset_seconds,
            },
        )
        self.async_write_ha_state()
