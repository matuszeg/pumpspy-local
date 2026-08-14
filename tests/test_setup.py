"""The Home Assistant layer: does the listener actually bind and relay?

Proven by hand against a real HA container first. These pin it down so the
config flow and entity platforms can be built on top without re-testing by hand.
"""

import asyncio
import socket
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import ClientSession
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pumpspy_local.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ during tests."""
    yield


@pytest.fixture
def expected_lingering_tasks() -> bool:
    """Downgrade the harness's lingering-task failure to a warning, here only.

    Replaying a GET that declares a body it never sends leaves aiohttp's
    connection handler alive until the peer disconnects, and it outlives the
    test by a beat. It is an artefact of driving the listener over a raw socket,
    not a leak in the integration: the runner is cleaned up in
    ``shutdown_listener`` below, and a real device sends Connection: close.

    Tradeoff worth knowing: while this is in place, a genuine task leak in this
    module would warn instead of fail.
    """
    return True


@pytest_asyncio.fixture(autouse=True)
async def shutdown_listener(hass):
    """Stop the listener between tests.

    Otherwise its connection handlers outlive the test and Home Assistant's
    harness rightly fails them as lingering tasks.
    """
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)


async def _setup(hass, upstream, port) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"port": port, "upstream": f"http://{upstream.host}:{upstream.port}"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def runtime_of(hass, entry) -> object:
    return hass.data[DOMAIN][entry.entry_id]


async def test_setup_binds_the_port_and_relays_to_upstream(hass, upstream, free_port):
    await _setup(hass, upstream, free_port)

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{free_port}/bbs_json", data=b'{"deviceid":"X"}'
        ) as response:
            body = await response.read()

    assert upstream.received["method"] == "POST"
    assert upstream.received["path"] == "/bbs_json"
    assert upstream.received["body"] == b'{"deviceid":"X"}'
    assert body == b"ok"


async def test_setup_rewrites_the_host_header_for_upstream(hass, upstream, free_port):
    """The device's Host header names us, not the vendor.

    Relaying it verbatim would announce a local address to the vendor, which is
    the kind of thing that gets a request rejected for no obvious reason.
    """
    await _setup(hass, upstream, free_port)

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/pings", data=b"{}"):
            pass

    assert upstream.received["headers"]["Host"] == f"{upstream.host}:{upstream.port}"


async def test_get_with_a_stale_content_length_does_not_hang(hass, upstream, free_port):
    """Real captures show the device sending Content-Length on GETs, with no body.

    Waiting for a body that never arrives wedges the request until the device
    gives up. It polls /new_firmware roughly every 13 seconds, so this is the
    hottest path there is: getting it wrong stalls the firmware check forever.

    Byte shape is taken from a real capture, with the device id replaced.
    """
    await _setup(hass, upstream, free_port)

    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    writer.write(
        b"GET /new_firmware/11111111111111 HTTP/1.1\r\n"
        b"Host: www.pumpspy.com:8081 \r\n"
        b"Content-Type: application/json;charset=UTF-8 \r\n"
        b"Content-Length: 148 \r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    await writer.drain()

    try:
        reply = await asyncio.wait_for(reader.read(1024), timeout=5)
    finally:
        writer.close()
        await writer.wait_closed()

    assert reply.startswith(b"HTTP/1.1 200"), reply[:120]
    assert upstream.received["path"] == "/new_firmware/11111111111111"


async def test_double_space_in_the_request_line_is_accepted(hass, upstream, free_port):
    """The device emits "POST  /bbs_json" with two spaces. Captured, verbatim.

    Stricter parsers reject this outright, so it is worth a test that will shout
    if the listener is ever swapped for one that does.
    """
    await _setup(hass, upstream, free_port)

    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    writer.write(
        b"POST  /bbs_json HTTP/1.1\r\n"
        b"Host: www.pumpspy.com:8081 \r\n"
        b"Content-Length: 2 \r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"{}"
    )
    await writer.drain()

    try:
        reply = await asyncio.wait_for(reader.read(1024), timeout=5)
    finally:
        writer.close()
        await writer.wait_closed()

    assert reply.startswith(b"HTTP/1.1 200"), reply[:120]
    assert upstream.received["path"] == "/bbs_json"


async def test_a_posted_reading_becomes_device_state(hass, upstream, free_port):
    """The end-to-end path: bytes on the wire become something an entity can read."""
    entry = await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    device = runtime_of(hass, entry).devices["11111111111111"]
    assert device.battery_volts == 13.324


async def test_a_parse_failure_does_not_break_forwarding(hass, upstream, free_port):
    """Parsing runs beside delivery to the vendor. It must never cost a delivery."""
    entry = await _setup(hass, upstream, free_port)

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{free_port}/bbs_json", data=b"utter nonsense"
        ) as response:
            assert response.status == 200
    await hass.async_block_till_done()

    assert upstream.received["body"] == b"utter nonsense"
    assert runtime_of(hass, entry).devices == {}


async def test_a_reading_creates_a_battery_voltage_entity(hass, upstream, free_port):
    """Devices are not configured; they are adopted when they first report."""
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    sensors = [
        state
        for state in hass.states.async_all("sensor")
        if "battery_voltage" in state.entity_id
    ]
    assert len(sensors) == 1
    assert sensors[0].state == "13.324"


async def test_entities_are_grouped_under_a_device(hass, upstream, free_port):
    """Home Assistant only registers devices for entities on a config entry.

    Under YAML setup the DeviceInfo was silently ignored and the sensors sat
    loose in the entity list, which is why setup moved to a config entry.
    """
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, "11111111111111")}
    )
    assert device is not None
    assert device.manufacturer == "Richtech"
    assert device.model == "PumpSpy / PitBoss+"


async def test_unloading_releases_the_port(hass, upstream, free_port):
    """A reconfigure or reload must not leave the port held."""
    entry = await _setup(hass, upstream, free_port)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", free_port))  # raises if the listener still holds it
