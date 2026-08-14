"""Shared fixtures."""

import socket

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer


@pytest.fixture
def free_port(socket_enabled) -> int:
    """A port nothing is listening on, so tests never collide with a dev instance."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest_asyncio.fixture
async def upstream(socket_enabled):
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
