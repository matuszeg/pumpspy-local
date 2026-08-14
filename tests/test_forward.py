"""Upstream forwarding must be byte-for-byte transparent.

The device's traffic reaches the vendor through us. Anything we change here
is a change the vendor sees, so these tests pin the relay down hard.
"""

import pytest
import pytest_asyncio
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.pumpspy_local.core.forward import ProxyRequest, forward


@pytest_asyncio.fixture
async def upstream():
    """A stand-in vendor server that records what it was sent.

    ``server.reply`` controls what it answers with.
    """
    received = {}
    reply = {"status": 200, "body": b"ok"}

    async def handler(request):
        received["method"] = request.method
        received["path"] = request.path
        received["body"] = await request.read()
        received["headers"] = dict(request.headers)
        return web.Response(status=reply["status"], body=reply["body"])

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    server.received = received
    server.reply = reply
    yield server
    await server.close()


async def test_forward_relays_method_path_and_body_unmodified(upstream):
    async with ClientSession() as session:
        await forward(
            session,
            f"http://{upstream.host}:{upstream.port}",
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
            f"http://{upstream.host}:{upstream.port}",
            ProxyRequest(method="GET", path="/pings", headers={}, body=b""),
        )

    assert response.status == 418
    assert response.body == b"i-am-a-teapot"
