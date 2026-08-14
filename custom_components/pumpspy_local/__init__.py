"""Local monitoring for PumpSpy / PitBoss+ sump pump battery backup systems.

Binds the port the device reports to, relays every request upstream unmodified,
returns the upstream reply, and turns what it sees into Home Assistant entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aiohttp import web
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_PORT,
    CONF_UPSTREAM,
    DOMAIN,
    SIGNAL_NEW_DEVICE,
    signal_device_update,
)
from .core.forward import ProxyRequest, forward
from .core.parser import BbsReading, parse_request
from .core.state import DeviceState

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

# Headers that describe this hop rather than the request, and must not be relayed.
_HOP_BY_HOP = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

# The device sends a stale "Content-Length: 148" on GETs and then no body at all.
# Waiting for that body wedges the request until the device gives up, and it polls
# /new_firmware every ~13 seconds, so never read a body on a method that cannot
# carry one. Observed in real captures.
_BODYLESS_METHODS = {"GET", "HEAD", "DELETE", "OPTIONS", "TRACE"}


@dataclass
class PumpspyRuntime:
    """What the integration keeps alive while it is loaded."""

    runner: web.AppRunner | None = None
    devices: dict[str, DeviceState] = field(default_factory=dict)

    def apply(self, reading: BbsReading) -> DeviceState:
        """Merge a reading into the state for its device, creating it if new."""
        device = self.devices.get(reading.device_id)
        if device is None:
            device = DeviceState(device_id=reading.device_id)
            self.devices[reading.device_id] = device
            _LOGGER.info("discovered device %s", reading.device_id)
        device.apply(reading)
        return device


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start the listener the device reports to."""
    port: int = entry.data[CONF_PORT]
    upstream: str = entry.data[CONF_UPSTREAM].rstrip("/")

    session = async_get_clientsession(hass)
    runtime = PumpspyRuntime()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    async def handle(request: web.Request) -> web.Response:
        proxied = ProxyRequest(
            method=request.method,
            path=request.rel_url.path_qs,
            headers={
                name: value
                for name, value in request.headers.items()
                if name.lower() not in _HOP_BY_HOP
            },
            body=b"" if request.method in _BODYLESS_METHODS else await request.read(),
        )
        _LOGGER.debug("device request: %s %s", proxied.method, proxied.path)

        response = await forward(session, upstream, proxied)
        _LOGGER.debug("upstream replied %s", response.status)

        # Parsed after forwarding, so the vendor's delivery is never delayed or
        # put at risk by it. parse_request never raises, and this is a few
        # microseconds of pure CPU on a ~100 byte body, so it stays inline
        # rather than becoming a task that would only add scheduling overhead.
        parsed = parse_request(proxied.path, proxied.body)
        if isinstance(parsed, BbsReading):
            is_new = parsed.device_id not in runtime.devices
            device = runtime.apply(parsed)
            if is_new:
                async_dispatcher_send(hass, SIGNAL_NEW_DEVICE, device)
            async_dispatcher_send(hass, signal_device_update(device.device_id))

        return web.Response(status=response.status, body=response.body)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    runtime.runner = runner
    _LOGGER.info("listening on :%s, forwarding to %s", port, upstream)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop the listener and release the port."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime: PumpspyRuntime = hass.data[DOMAIN].pop(entry.entry_id)
        if runtime.runner is not None:
            await runtime.runner.cleanup()
        _LOGGER.info("listener stopped")
    return unloaded
