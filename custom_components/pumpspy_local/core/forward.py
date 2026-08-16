"""Transparent forwarding of the device's requests to the vendor."""

from __future__ import annotations

from dataclasses import dataclass

from aiohttp import ClientSession, ServerDisconnectedError, TCPConnector

from .upstream import Target


def upstream_session() -> ClientSession:
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
    return ClientSession(connector=TCPConnector(force_close=True))


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
