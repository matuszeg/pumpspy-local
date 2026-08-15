"""Transparent forwarding of the device's requests to the vendor."""

from __future__ import annotations

from dataclasses import dataclass

from aiohttp import ClientSession

from .upstream import Target


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
    """
    async with session.request(
        request.method,
        f"{target.base_url}{request.path}",
        headers={**request.headers, "Host": target.host_header},
        data=request.body,
    ) as response:
        return ProxyResponse(status=response.status, body=await response.read())
