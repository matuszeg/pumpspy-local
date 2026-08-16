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
    requests: list[str] = []
    reply = {"status": 200, "body": b"ok"}

    async def handler(request):
        received["method"] = request.method
        received["path"] = request.path
        received["body"] = await request.read()
        received["headers"] = dict(request.headers)
        requests.append(request.path)
        response = web.Response(status=reply["status"], body=reply["body"])
        # The proxy correctly strips Connection: close as a hop-by-hop header, so
        # its upstream connection would otherwise stay keep-alive and leave this
        # server's handler task alive past the end of the test.
        response.force_close()
        return response

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    server.received = received
    server.requests = requests
    server.reply = reply
    yield server
    await server.close()


@pytest_asyncio.fixture
async def keepalive_upstream(socket_enabled):
    """A stand-in vendor that keeps connections alive and records their ports.

    Deliberately does not force_close: that is what lets a client pool a
    connection and reuse it, which is the behaviour under test.
    """
    peer_ports: list[int] = []

    async def handler(request):
        await request.read()
        peer_ports.append(request.transport.get_extra_info("peername")[1])
        return web.Response(status=200, body=b"ok")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    server.peer_ports = peer_ports
    yield server
    await server.close()
