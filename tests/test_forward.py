"""Upstream forwarding must be byte-for-byte transparent.

The device's traffic reaches the vendor through us. Anything we change here
is a change the vendor sees, so these tests pin the relay down hard.
"""

import asyncio
import time

import pytest
from aiohttp import ClientSession

from custom_components.pumpspy_local.core.forward import (
    VENDOR_TIMEOUT_SECONDS,
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


async def test_a_vendor_that_hangs_up_without_answering_is_retried_once(
    flaky_upstream,
):
    """The vendor does this for real, and the device pays for it otherwise.

    Captured on the wire: the vendor accepts the connection, takes the request,
    then sends FIN with no response. Turning that into an error hands the
    failure to the device, which retries only three times before dropping the
    event for good. Nothing was answered, so the request was not processed and
    replaying it is safe.
    """
    async with upstream_session() as session:
        response = await forward(
            session,
            Target(
                base_url=f"http://{flaky_upstream.host}:{flaky_upstream.port}",
                host_header="www.pumpspy.com:8081",
            ),
            ProxyRequest(
                method="POST",
                path="/bbs_json",
                headers={"Content-Type": "application/json"},
                body=b'{"deviceid":1}',
            ),
        )

    assert response.status == 200
    assert flaky_upstream.state["requests"] == 2, "should have tried exactly twice"


def test_the_vendor_is_given_up_on_before_the_device_gives_up_on_us():
    """The budget is set against the device's behaviour, not by taste.

    The device abandons a request after about ten seconds -- seen as
    ``device_got=499`` at 10.003 s in the proxy's access log. Since #21 a token
    request is relayed first and only answered locally if that relay fails, so
    a vendor that hangs rather than refuses would hold the locally minted
    answer past the point where the device has stopped listening for it. The
    answer whose entire value is arriving in time would not arrive at all.

    Two attempts have to fit, since a hangup on the first is retried.
    """
    assert VENDOR_TIMEOUT_SECONDS * 2 < 10


async def test_a_vendor_that_hangs_does_not_hold_the_request_open(
    hanging_upstream,
):
    """aiohttp's default is five minutes, which is not a wait anyone survives."""
    started = time.monotonic()

    async with upstream_session(timeout_seconds=0.3) as session:
        with pytest.raises(asyncio.TimeoutError):
            await forward(
                session,
                Target(
                    base_url=f"http://{hanging_upstream.host}:{hanging_upstream.port}",
                    host_header="www.pumpspy.com:8081",
                ),
                ProxyRequest(
                    method="POST",
                    path="/oauth/token",
                    headers={"Content-Type": "application/json"},
                    body=b"{}",
                ),
            )

    assert time.monotonic() - started < 2


async def test_a_hang_is_not_retried_the_way_a_hangup_is(hanging_upstream):
    """The retry exists for a vendor that answers nothing *quickly* -- a FIN
    with no response, where replaying costs milliseconds. Replaying a hang buys
    nothing and doubles the only thing that matters here, which is the wait."""
    async with upstream_session(timeout_seconds=0.3) as session:
        with pytest.raises(asyncio.TimeoutError):
            await forward(
                session,
                Target(
                    base_url=f"http://{hanging_upstream.host}:{hanging_upstream.port}",
                    host_header="www.pumpspy.com:8081",
                ),
                ProxyRequest(
                    method="POST",
                    path="/bbs_json",
                    headers={"Content-Type": "application/json"},
                    body=b"{}",
                ),
            )

    assert hanging_upstream.state["requests"] == 1
