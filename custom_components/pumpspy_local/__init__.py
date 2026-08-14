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
from .core.parser import BbsReading, Ping, parse_request
from .core.state import DeviceState

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

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

    def device_for(self, device_id: str) -> tuple[DeviceState, bool]:
        """The state for a device, creating it the first time it reports."""
        device = self.devices.get(device_id)
        if device is not None:
            return device, False

        device = DeviceState(device_id=device_id)
        self.devices[device_id] = device
        _LOGGER.info("discovered device %s", device_id)
        return device, True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start the listener the device reports to."""
    port: int = entry.data[CONF_PORT]
    upstream: str = entry.data[CONF_UPSTREAM].rstrip("/")

    session = async_get_clientsession(hass)
    runtime = PumpspyRuntime()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    def _record(runtime: PumpspyRuntime, parsed: object) -> None:
        """Fold a parsed message into device state and tell the entities."""
        touched: list[tuple[DeviceState, bool]] = []

        if isinstance(parsed, BbsReading):
            device, is_new = runtime.device_for(parsed.device_id)
            device.apply(parsed)
            touched.append((device, is_new))
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, Ping):
                    device, is_new = runtime.device_for(item.device_id)
                    device.apply_ping(item)
                    touched.append((device, is_new))

        for device, is_new in touched:
            if is_new:
                async_dispatcher_send(hass, SIGNAL_NEW_DEVICE, device)
            async_dispatcher_send(hass, signal_device_update(device.device_id))

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
        _record(runtime, parse_request(proxied.path, proxied.body))

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
