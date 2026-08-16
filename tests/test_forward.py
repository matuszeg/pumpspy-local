"""Upstream forwarding must be byte-for-byte transparent.

The device's traffic reaches the vendor through us. Anything we change here
is a change the vendor sees, so these tests pin the relay down hard.
"""

from aiohttp import ClientSession

from custom_components.pumpspy_local.core.forward import (
    ProxyRequest,
    forward,
    upstream_session,
)
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


async def test_each_forward_opens_a_fresh_upstream_connection(keepalive_upstream):
    """Never reuse a connection to the vendor.

    A pooled connection sits idle between the device's messages -- minutes at a
    time -- and the vendor eventually hangs it up. When its close crosses our
    next request on the wire, aiohttp raises ServerDisconnectedError, and it
    will not silently replay a POST, so that telemetry never reaches the vendor.

    Seen in production: intermittent "Server disconnected" warnings starting
    about forty minutes after go-live, quietly dropping the vendor's copy of
    real pump data while the local entities carried on looking healthy. The
    race itself is timing-dependent and not honestly reproducible here, so this
    pins the property that makes it impossible instead.
    """
    target = Target(
        base_url=f"http://{keepalive_upstream.host}:{keepalive_upstream.port}",
        host_header="www.pumpspy.com:8081",
    )
    request = ProxyRequest(
        method="POST",
        path="/pings",
        headers={"Content-Type": "application/json"},
        body=b'[{"deviceid":1}]',
    )

    async with upstream_session() as session:
        for _ in range(3):
            await forward(session, target, request)

    ports = keepalive_upstream.peer_ports
    assert len(set(ports)) == 3, f"connection was reused: {ports}"
