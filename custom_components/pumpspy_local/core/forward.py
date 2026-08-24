"""Transparent forwarding of the device's requests to the vendor."""

from __future__ import annotations

from dataclasses import dataclass

from aiohttp import (
    ClientSession,
    ClientTimeout,
    ServerDisconnectedError,
    TCPConnector,
)

from .upstream import Target


# How long to wait for the vendor before giving up on a request.
#
# Set against the device, not by taste. It abandons a request after about ten
# seconds -- visible as ``device_got=499`` at 10.003 s in the proxy's access log
# -- and since #21 a token request is relayed to the vendor first and answered
# locally only if that relay fails. A vendor that hangs rather than refuses
# would therefore hold the locally minted answer past the point where the device
# has stopped listening, which is the one thing that answer exists to beat.
#
# Four seconds is roughly three times the slowest healthy vendor response
# measured at the shim (0.1-1.3 s), and leaves room for the retry below without
# either attempt approaching the device's limit.
VENDOR_TIMEOUT_SECONDS = 4


def upstream_session(timeout_seconds: float | None = None) -> ClientSession:
    """A session for vendor traffic that never reuses a connection.

    Home Assistant's shared session pools connections, which is right for
    ordinary integrations and wrong here. The device reports every couple of
    minutes, so a pooled connection to the vendor sits idle far longer than the
    vendor is willing to hold it open. When the vendor's close crosses our next
    request on the wire the request fails outright -- and aiohttp will not
    replay a POST, so that message never reaches the vendor at all.

    A fresh connection per request costs one handshake and removes the race.
    It is also what the device itself does: every request it sends carries
    Connection: close.
    """
    # Resolved here rather than as a default argument so a test can shorten
    # it without waiting out the real budget.
    if timeout_seconds is None:
        timeout_seconds = VENDOR_TIMEOUT_SECONDS
    return ClientSession(
        connector=TCPConnector(force_close=True),
        timeout=ClientTimeout(total=timeout_seconds),
    )


@dataclass(frozen=True)
class ProxyRequest:
    """A request as received from the device."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class ProxyResponse:
    """A response as received from the vendor."""

    status: int
    body: bytes


async def forward(
    session: ClientSession, target: Target, request: ProxyRequest
) -> ProxyResponse:
    """Relay ``request`` to ``target`` unmodified and return the reply.

    The one header we set ourselves is ``Host``. The connection is made to an
    address rather than a name -- that is what keeps the redirect out of the
    path -- so without this the vendor would be addressed by IP and a
    name-based host would not recognise the request.

    A vendor that *hangs* is not retried, only one that hangs up. The retry is
    worth it when nothing was answered quickly -- replaying costs milliseconds
    -- but replaying a hang buys nothing and doubles the wait, which here is the
    only thing that matters. This falls out of catching ServerDisconnectedError
    alone: a timeout raises TimeoutError and leaves immediately.

    Retried once if the vendor hangs up without answering. It does this for
    real, intermittently: captured on the wire, it accepts the connection,
    takes the request, then sends FIN with no response. Passing that failure on
    means the device retries instead, and it only tries three times before
    dropping the event for good -- so a second vendor hiccup costs real pump
    data. Nothing was answered, so the request was not processed and replaying
    it is safe.
    """
    for attempt in (1, 2):
        try:
            async with session.request(
                request.method,
                f"{target.base_url}{request.path}",
                headers={**request.headers, "Host": target.host_header},
                data=request.body,
            ) as response:
                return ProxyResponse(
                    status=response.status, body=await response.read()
                )
        except ServerDisconnectedError:
            if attempt == 2:
                raise

    raise AssertionError("unreachable")
