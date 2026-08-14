"""Transparent forwarding of the device's requests to the vendor."""

from __future__ import annotations

from dataclasses import dataclass

from aiohttp import ClientSession


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
    session: ClientSession, upstream_base: str, request: ProxyRequest
) -> ProxyResponse:
    """Relay ``request`` to ``upstream_base`` unmodified and return the reply."""
    async with session.request(
        request.method,
        f"{upstream_base}{request.path}",
        headers=request.headers,
        data=request.body,
    ) as response:
        return ProxyResponse(status=response.status, body=await response.read())
