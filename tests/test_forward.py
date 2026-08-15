"""Upstream forwarding must be byte-for-byte transparent.

The device's traffic reaches the vendor through us. Anything we change here
is a change the vendor sees, so these tests pin the relay down hard.
"""

from aiohttp import ClientSession

from custom_components.pumpspy_local.core.forward import ProxyRequest, forward
from custom_components.pumpspy_local.core.upstream import Target


async def test_forward_relays_method_path_and_body_unmodified(upstream):
    async with ClientSession() as session:
        await forward(
            session,
            Target(
                base_url=f"http://{upstream.host}:{upstream.port}",
                host_header="www.pumpspy.com:8081",
            ),
            ProxyRequest(
                method="POST",
                path="/bbs_json",
                headers={"Content-Type": "application/json"},
                body=b'{"deviceid":"123","json":"{}"}',
            ),
        )

    assert upstream.received["method"] == "POST"
    assert upstream.received["path"] == "/bbs_json"
    assert upstream.received["body"] == b'{"deviceid":"123","json":"{}"}'


async def test_forward_returns_the_upstream_response_to_the_caller(upstream):
    upstream.reply["status"] = 418
    upstream.reply["body"] = b"i-am-a-teapot"

    async with ClientSession() as session:
        response = await forward(
            session,
            Target(
                base_url=f"http://{upstream.host}:{upstream.port}",
                host_header="www.pumpspy.com:8081",
            ),
            ProxyRequest(method="GET", path="/pings", headers={}, body=b""),
        )

    assert response.status == 418
    assert response.body == b"i-am-a-teapot"


async def test_forward_presents_the_vendor_hostname_when_connecting_by_address(
    upstream,
):
    # Connecting by IP is what keeps the poisoned resolver out of the path, but
    # the vendor serves by name, so the name has to survive in the Host header.
    async with ClientSession() as session:
        await forward(
            session,
            Target(
                base_url=f"http://{upstream.host}:{upstream.port}",
                host_header="www.pumpspy.com:8081",
            ),
            ProxyRequest(method="GET", path="/pings", headers={}, body=b""),
        )

    assert upstream.received["headers"]["Host"] == "www.pumpspy.com:8081"
