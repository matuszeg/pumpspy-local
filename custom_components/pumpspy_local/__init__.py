"""Local monitoring for PumpSpy / PitBoss+ sump pump battery backup systems.

Binds the port the device reports to, relays every request upstream unmodified,
returns the upstream reply, and turns what it sees into Home Assistant entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from aiohttp import ClientError, ClientSession, web
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CHECK_INTERVAL_HOURS,
    CONF_FIRMWARE_POLICY,
    CONF_FLOW_RATE,
    CONF_NAMESERVER,
    CONF_PORT,
    CONF_UPSTREAM,
    CONF_UPSTREAM_IP,
    DEFAULT_CHECK_INTERVAL_HOURS,
    DEFAULT_FIRMWARE_POLICY,
    DOMAIN,
    FIRMWARE_PATH,
    POLICY_QUARANTINE,
    SERVICE_DEVICE_NAME,
    SIGNAL_FIRMWARE,
    SIGNAL_NEW_DEVICE,
    SIGNAL_VENDOR,
    signal_device_update,
    signal_pump_run,
)
from .core.forward import ProxyRequest, ProxyResponse, forward, upstream_session
from .core.firmware import FirmwareChecker, Reply, Verdict, classify
from .core.gallons import DEFAULT_FLOW_RATE
from .core.parser import BbsReading, Ping, parse_request
from .core.state import DeviceState
from .core.upstream import (
    DEFAULT_NAMESERVER,
    UpstreamAddress,
    UpstreamUnavailable,
    aiodns_resolver,
)
from .core.vendor import VendorHealth

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SENSOR,
]

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


STORAGE_VERSION = 1

# Long enough that a burst of messages is one write, short enough that a hard
# power cut to the whole machine loses very little.
SAVE_DELAY_SECONDS = 10


@dataclass
class PumpspyRuntime:
    """What the integration keeps alive while it is loaded."""

    store: Store | None = None
    runner: web.AppRunner | None = None
    # Ours, not Home Assistant's shared one, so unload has to close it.
    session: ClientSession | None = None
    devices: dict[str, DeviceState] = field(default_factory=dict)
    # One verdict per entry, not per device: the vendor is reached over one
    # connection regardless of how many devices report through it.
    vendor: VendorHealth = field(default_factory=VendorHealth)
    flow_rate: float = DEFAULT_FLOW_RATE
    check_interval: timedelta = timedelta(hours=DEFAULT_CHECK_INTERVAL_HOURS)
    # Keyed by device id: the endpoint is /new_firmware/<id>, so two devices
    # could legitimately be offered different firmware and must not share a
    # cached reply.
    firmware: dict[str, FirmwareChecker] = field(default_factory=dict)

    def firmware_for(self, device_id: str) -> FirmwareChecker:
        if device_id not in self.firmware:
            self.firmware[device_id] = FirmwareChecker(interval=self.check_interval)
        return self.firmware[device_id]

    def as_stored(self) -> dict:
        return {
            "devices": {
                device_id: device.to_stored()
                for device_id, device in self.devices.items()
            }
        }

    def request_save(self) -> None:
        """Ask for the current state to be written out shortly."""
        if self.store is not None:
            self.store.async_delay_save(self.as_stored, SAVE_DELAY_SECONDS)

    def device_for(self, device_id: str) -> tuple[DeviceState, bool]:
        """The state for a device, creating it the first time it reports."""
        device = self.devices.get(device_id)
        if device is not None:
            return device, False

        device = DeviceState(device_id=device_id, flow_rate=self.flow_rate)
        self.devices[device_id] = device
        _LOGGER.info("discovered device %s", device_id)
        return device, True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start the listener the device reports to."""
    port: int = entry.data[CONF_PORT]
    upstream: str = entry.data[CONF_UPSTREAM].rstrip("/")
    flow_rate: float = entry.data.get(CONF_FLOW_RATE, DEFAULT_FLOW_RATE)
    quarantine: bool = (
        entry.data.get(CONF_FIRMWARE_POLICY, DEFAULT_FIRMWARE_POLICY)
        == POLICY_QUARANTINE
    )
    check_hours: int = entry.data.get(
        CONF_CHECK_INTERVAL_HOURS, DEFAULT_CHECK_INTERVAL_HOURS
    )

    # The redirect that brings the device here is a fact about DNS, not about
    # the device, so this host sees it too. Locate the vendor out of band.
    upstream_address = UpstreamAddress(
        url=upstream,
        resolve=aiodns_resolver(
            [entry.data.get(CONF_NAMESERVER) or DEFAULT_NAMESERVER]
        ),
        override=entry.data.get(CONF_UPSTREAM_IP) or None,
    )

    session = upstream_session()
    store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    runtime = PumpspyRuntime(
        store=store,
        session=session,
        flow_rate=flow_rate,
        check_interval=timedelta(hours=check_hours),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    # Registered here rather than left to the entity that lives on it, because
    # the pumps name it as their via_device. A device pointing at one Home
    # Assistant has not seen yet silently loses the link -- and on a restart
    # every pump entity is built from restored state before any message
    # arrives, with the platforms set up concurrently, so it would be a race.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        entry_type=dr.DeviceEntryType.SERVICE,
        name=SERVICE_DEVICE_NAME,
    )

    # Restore what the device only tells us when it changes. Without this a
    # restart leaves the alarms reading unknown until the next real event.
    for device_id, stored in (await store.async_load() or {}).get("devices", {}).items():
        restored = DeviceState.from_stored(device_id, stored)
        restored.flow_rate = runtime.flow_rate
        runtime.devices[device_id] = restored
        _LOGGER.debug("restored device %s", device_id)

    def _record(runtime: PumpspyRuntime, parsed: object) -> None:
        """Fold a parsed message into device state and tell the entities."""
        touched: list[tuple[DeviceState, bool]] = []

        if isinstance(parsed, BbsReading):
            device, is_new = runtime.device_for(parsed.device_id)
            # Local date for the daily rollover, UTC instant for timestamping a
            # self-test -- one is a calendar question, the other a point in time.
            device.apply(
                parsed, today=dt_util.now().date(), now=dt_util.utcnow()
            )
            touched.append((device, is_new))
            if parsed.pump_run is not None:
                # Set before dispatching: on a device's first message the event
                # entity does not exist yet, and picks this up when it is added.
                device.unfired_run = parsed.pump_run
                async_dispatcher_send(
                    hass, signal_pump_run(device.device_id), parsed.pump_run
                )
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

        if touched:
            runtime.request_save()

    async def _relay(proxied: ProxyRequest) -> ProxyResponse | None:
        """Deliver one request to the vendor, or None if it could not be sent.

        Both failures are survivable and neither is worth an exception escaping
        into the listener: the device retries, and what we learned from the
        message is ours either way.
        """
        try:
            target = await upstream_address.target(dt_util.utcnow())
            response = await forward(session, target, proxied)
        except UpstreamUnavailable as err:
            # A configuration problem, not a passing outage -- say so loudly.
            _LOGGER.error("not forwarding: %s", err)
            runtime.vendor.record_failure(str(err))
        except ClientError as err:
            _LOGGER.warning("could not reach the vendor: %s", err)
            runtime.vendor.record_failure(str(err))
        else:
            runtime.vendor.record_success(dt_util.utcnow())
            async_dispatcher_send(hass, SIGNAL_VENDOR, entry.entry_id)
            return response

        async_dispatcher_send(hass, SIGNAL_VENDOR, entry.entry_id)
        return None

    async def _handle_firmware_check(proxied: ProxyRequest) -> web.Response:
        """Answer the device's firmware poll, asking upstream only when due.

        The device asks every ~13 seconds; from its point of view nothing here
        changes, it just gets the same answer without the vendor being asked
        every time.
        """
        # /new_firmware/<device id>
        device_id = proxied.path.rsplit("/", 1)[-1]
        checker = runtime.firmware_for(device_id)

        if checker.should_query_upstream(dt_util.utcnow()):
            response = await _relay(proxied)
            if response is None:
                # Answer from the last known-good reply rather than making the
                # vendor's bad day the device's problem. last_checked is left
                # alone, so the next poll tries again.
                cached = checker.reply_for_device()
                if cached is None:
                    return web.Response(status=502)
                return web.Response(status=cached.status, body=cached.body)

            reply = Reply(status=response.status, body=response.body)
            verdict = classify(reply.status, reply.body)
            served = checker.record_upstream(
                dt_util.utcnow(), reply, quarantine=quarantine
            )
            if verdict is Verdict.UPDATE_OFFERED:
                _LOGGER.warning(
                    "vendor is offering a firmware update%s",
                    " -- held, awaiting approval" if checker.held else "",
                )
                async_dispatcher_send(hass, SIGNAL_FIRMWARE, device_id)
        else:
            served = checker.reply_for_device()
            _LOGGER.debug("firmware check answered from cache")

        return web.Response(status=served.status, body=served.body)

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

        if proxied.path.startswith(FIRMWARE_PATH):
            return await _handle_firmware_check(proxied)

        response = await _relay(proxied)

        # Parsed after forwarding, so the vendor's delivery is never delayed or
        # put at risk by it -- but always parsed, including when the delivery
        # failed. Local monitoring that stops when the vendor is unreachable
        # would be missing the point. parse_request never raises, and this is a
        # few microseconds of pure CPU on a ~100 byte body, so it stays inline
        # rather than becoming a task that would only add scheduling overhead.
        _record(runtime, parse_request(proxied.path, proxied.body))

        if response is None:
            # Not a synthetic 200: telling the device its event was delivered
            # when it was not is how an event gets lost for good.
            return web.Response(status=502)

        _LOGGER.debug("upstream replied %s", response.status)
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
        if runtime.store is not None:
            # Flush rather than leaving it to the delayed save, which would drop
            # anything reported in the last few seconds.
            await runtime.store.async_save(runtime.as_stored())
        if runtime.runner is not None:
            await runtime.runner.cleanup()
        if runtime.session is not None:
            await runtime.session.close()
        _LOGGER.info("listener stopped")
    return unloaded
