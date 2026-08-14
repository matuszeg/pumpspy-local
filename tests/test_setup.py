"""The Home Assistant layer: does the listener actually bind and relay?

Proven by hand against a real HA container first. These pin it down so the
config flow and entity platforms can be built on top without re-testing by hand.
"""

import pytest
from aiohttp import ClientSession
from homeassistant.setup import async_setup_component

from custom_components.pumpspy_local import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ during tests."""
    yield


async def _setup(hass, upstream, port) -> None:
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "port": port,
                "upstream": f"http://{upstream.host}:{upstream.port}",
            }
        },
    )
    await hass.async_block_till_done()


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
