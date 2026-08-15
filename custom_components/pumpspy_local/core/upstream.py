"""Finding the vendor while the device's DNS points at us.

Interception works by answering ``www.pumpspy.com`` with this host's address on
the device's network. Nothing stops that answer from reaching this host too, so
resolving the upstream the ordinary way returns *us*, and every forwarded
request lands back in our own listener. This module resolves the vendor out of
band instead.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import aiodns

# Given a hostname, the addresses it resolves to.
Resolve = Callable[[str], Awaitable[list[str]]]

# Used when nothing else is configured. The point is only that it is not the
# resolver the redirect is installed in.
DEFAULT_NAMESERVER = "1.1.1.1"


def aiodns_resolver(nameservers: Sequence[str]) -> Resolve:
    """DNS that deliberately bypasses whatever this host is configured to use.

    The host's own resolver is the one carrying the redirect, so asking it for
    the vendor's name returns this machine.
    """
    resolver = aiodns.DNSResolver(nameservers=list(nameservers))

    async def resolve(hostname: str) -> list[str]:
        return [answer.host for answer in await resolver.query(hostname, "A")]

    return resolve


class UpstreamUnavailable(Exception):
    """The vendor's address could not be established."""


class UpstreamLoop(UpstreamUnavailable):
    """The upstream resolved to somewhere on this network -- i.e. to us."""


@dataclass(frozen=True)
class Target:
    """Where to send one forwarded request."""

    # Addressed by IP, so the answer never comes from the poisoned resolver.
    base_url: str
    # ...but the vendor is name-based, so it still has to see its own hostname.
    host_header: str


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _refuse_if_local(hostname: str, address: str) -> None:
    """Reject a lookup that answered with somewhere on this network.

    The vendor is a public host, so a private, loopback or link-local answer for
    its name is the redirect coming back at us -- not a place worth trying. This
    is checked on *looked up* addresses only: an address that was configured by
    hand was chosen deliberately, and a development instance forwards to a
    stand-in on the LAN on purpose.
    """
    parsed = ipaddress.ip_address(address)
    if parsed.is_global:
        return

    raise UpstreamLoop(
        f"{hostname} resolved to {address}, which is not a public address. "
        "The DNS redirect that points the device at Home Assistant is being "
        "answered for Home Assistant too, so forwarding there would loop back "
        "into this listener. Set an explicit upstream address, or point the "
        "integration at a resolver that is not affected by the redirect."
    )


@dataclass
class UpstreamAddress:
    """The configured upstream, resolved without the host's own DNS."""

    url: str
    resolve: Resolve
    # An explicit address for the upstream, for when out-of-band DNS is not
    # available. Set, it is trusted as given and never looked up.
    override: str | None = None
    # The vendor's address is not going to move often, and the device reports
    # every few seconds, so a lookup per request would be pure noise.
    ttl: timedelta = timedelta(hours=1)
    # The device is waiting while this happens, and it gives up and re-polls on
    # its own schedule, so failing fast beats waiting out a resolver's own
    # generous default.
    lookup_timeout: timedelta = timedelta(seconds=3)
    _cached: str | None = field(default=None, init=False)
    _looked_up: datetime | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        self.scheme = parts.scheme
        self.hostname = parts.hostname or ""
        self.port = parts.port or (443 if parts.scheme == "https" else 80)

    @property
    def host_header(self) -> str:
        return f"{self.hostname}:{self.port}"

    async def target(self, now: datetime) -> Target:
        """The address to reach the vendor at right now."""
        address = self.override
        if address is None and _is_address(self.hostname):
            # Configured as an address already: nothing to look up.
            address = self.hostname
        if address is None:
            address = await self._looked_up_address(now)

        return Target(
            base_url=f"{self.scheme}://{address}:{self.port}",
            host_header=self.host_header,
        )

    async def _looked_up_address(self, now: datetime) -> str:
        if self._cached is not None and self._looked_up is not None:
            if now - self._looked_up < self.ttl:
                return self._cached

        try:
            async with asyncio.timeout(self.lookup_timeout.total_seconds()):
                address = (await self.resolve(self.hostname))[0]
        # Deliberately broad: resolvers fail in library-specific ways, and every
        # one of them means the same thing here.
        except Exception as err:
            if self._cached is not None:
                # A resolver having a bad moment is not a reason to stop
                # delivering the device's traffic to the vendor.
                return self._cached
            raise UpstreamUnavailable(
                f"could not resolve {self.hostname}: {err}"
            ) from err

        _refuse_if_local(self.hostname, address)
        self._cached = address
        self._looked_up = now
        return address
