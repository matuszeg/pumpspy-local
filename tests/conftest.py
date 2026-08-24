"""Shared fixtures."""

import asyncio
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


@pytest_asyncio.fixture
async def hanging_upstream(socket_enabled):
    """A vendor that accepts the request and then never answers.

    The other shape of the same outage. A hangup fails fast and the retry
    absorbs it; a hang holds the connection open for as long as we are willing
    to wait, which is the case that can outlast the device's patience.
    """
    state = {"requests": 0}
    stop = asyncio.Event()
    handlers: set[asyncio.Task] = set()

    async def handle(reader, writer):
        state["requests"] += 1
        handlers.add(asyncio.current_task())
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
            pass
        # Hold the connection open and say nothing at all, until teardown.
        await stop.wait()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    server.host, server.port = server.sockets[0].getsockname()
    server.state = state
    yield server
    stop.set()
    if handlers:
        await asyncio.gather(*handlers, return_exceptions=True)
    server.close()
    await server.wait_closed()


@pytest_asyncio.fixture
async def flaky_upstream(socket_enabled):
    """A vendor that hangs up on the first request without answering.

    Exactly what the real one does, intermittently: it accepts the connection,
    reads the request, then sends FIN with no response at all. Captured on the
    wire against the live endpoint.
    """
    state = {"requests": 0}

    async def handle(reader, writer):
        state["requests"] += 1
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            length = 0
            for line in head.decode(errors="replace").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":")[1])
            if length:
                await reader.readexactly(length)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
            pass

        if state["requests"] == 1:
            writer.close()  # hang up, no response
            return

        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        try:
            await writer.drain()
        except OSError:
            pass
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    server.host, server.port = server.sockets[0].getsockname()
    server.state = state
    yield server
    server.close()
