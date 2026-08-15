"""Finding the vendor while the device's DNS is poisoned.

The whole interception depends on ``www.pumpspy.com`` resolving to *us* on the
device's network. That same rewrite is visible to Home Assistant, so resolving
the upstream normally makes us forward into our own listener -- an unbounded
loop, and the vendor never gets the data. These tests pin down resolving it
out of band instead, and refusing to forward when the answer is obviously the
poisoned one.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.pumpspy_local.core.upstream import (
    UpstreamAddress,
    UpstreamLoop,
    UpstreamUnavailable,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
VENDOR = "http://www.pumpspy.com:8081"


def resolver(answers: dict[str, list[str]], calls: list[str] | None = None):
    """A stand-in for DNS that answers from a dict and records what it was asked."""

    async def resolve(hostname: str) -> list[str]:
        if calls is not None:
            calls.append(hostname)
        if hostname not in answers:
            raise OSError(f"no answer for {hostname}")
        return answers[hostname]

    return resolve


async def test_a_hostname_is_reached_by_its_resolved_address():
    upstream = UpstreamAddress(
        url=VENDOR, resolve=resolver({"www.pumpspy.com": ["206.80.104.221"]})
    )

    target = await upstream.target(NOW)

    assert target.base_url == "http://206.80.104.221:8081"


async def test_the_host_header_keeps_the_configured_hostname_and_port():
    upstream = UpstreamAddress(
        url=VENDOR, resolve=resolver({"www.pumpspy.com": ["206.80.104.221"]})
    )

    target = await upstream.target(NOW)

    assert target.host_header == "www.pumpspy.com:8081"


async def test_an_upstream_given_as_an_address_is_used_without_a_lookup():
    calls: list[str] = []
    upstream = UpstreamAddress(
        url="http://192.168.7.57:9099", resolve=resolver({}, calls)
    )

    target = await upstream.target(NOW)

    assert target.base_url == "http://192.168.7.57:9099"
    assert calls == []


async def test_an_explicit_address_override_is_used_without_a_lookup():
    calls: list[str] = []
    upstream = UpstreamAddress(
        url=VENDOR, resolve=resolver({}, calls), override="206.80.104.221"
    )

    target = await upstream.target(NOW)

    assert target.base_url == "http://206.80.104.221:8081"
    # The vendor still has to see the name it serves, not the address.
    assert target.host_header == "www.pumpspy.com:8081"
    assert calls == []


async def test_a_private_answer_for_the_vendor_is_refused_as_a_loop():
    # What the AdGuard rewrite actually does: the vendor's name now answers with
    # the Home Assistant host. Forwarding there is forwarding into ourselves.
    upstream = UpstreamAddress(
        url=VENDOR, resolve=resolver({"www.pumpspy.com": ["192.168.7.57"]})
    )

    with pytest.raises(UpstreamLoop) as raised:
        await upstream.target(NOW)

    assert "www.pumpspy.com" in str(raised.value)
    assert "192.168.7.57" in str(raised.value)


async def test_a_loopback_answer_is_refused_as_a_loop():
    upstream = UpstreamAddress(
        url=VENDOR, resolve=resolver({"www.pumpspy.com": ["127.0.0.1"]})
    )

    with pytest.raises(UpstreamLoop):
        await upstream.target(NOW)


async def test_a_public_answer_is_not_a_loop():
    upstream = UpstreamAddress(
        url=VENDOR, resolve=resolver({"www.pumpspy.com": ["206.80.104.221"]})
    )

    assert (await upstream.target(NOW)).base_url == "http://206.80.104.221:8081"


async def test_an_explicitly_configured_private_address_is_allowed():
    # A development instance forwards to a stand-in on the LAN. The guard is
    # about answers we were *given* by DNS, not addresses that were chosen.
    upstream = UpstreamAddress(
        url=VENDOR, resolve=resolver({}), override="192.168.7.57"
    )

    assert (await upstream.target(NOW)).base_url == "http://192.168.7.57:8081"


async def test_the_address_is_looked_up_once_and_reused():
    # The device reports every few seconds; a lookup per request would be a lot
    # of pointless DNS for an address that effectively never changes.
    calls: list[str] = []
    upstream = UpstreamAddress(
        url=VENDOR, resolve=resolver({"www.pumpspy.com": ["206.80.104.221"]}, calls)
    )

    await upstream.target(NOW)
    await upstream.target(NOW + timedelta(seconds=13))

    assert calls == ["www.pumpspy.com"]


async def test_the_address_is_looked_up_again_once_it_goes_stale():
    calls: list[str] = []
    answers = {"www.pumpspy.com": ["206.80.104.221"]}
    upstream = UpstreamAddress(url=VENDOR, resolve=resolver(answers, calls))

    await upstream.target(NOW)
    answers["www.pumpspy.com"] = ["206.80.104.222"]
    target = await upstream.target(NOW + upstream.ttl)

    assert calls == ["www.pumpspy.com", "www.pumpspy.com"]
    assert target.base_url == "http://206.80.104.222:8081"


async def test_a_failed_lookup_keeps_using_the_last_good_address():
    # Losing DNS is not a reason to stop delivering the device's traffic.
    answers = {"www.pumpspy.com": ["206.80.104.221"]}
    upstream = UpstreamAddress(url=VENDOR, resolve=resolver(answers))

    await upstream.target(NOW)
    del answers["www.pumpspy.com"]
    target = await upstream.target(NOW + upstream.ttl)

    assert target.base_url == "http://206.80.104.221:8081"


async def test_a_failed_lookup_with_nothing_to_fall_back_on_is_reported():
    upstream = UpstreamAddress(url=VENDOR, resolve=resolver({}))

    with pytest.raises(UpstreamUnavailable):
        await upstream.target(NOW)


async def test_a_resolver_that_hangs_does_not_hold_the_request_open():
    """The device is waiting on the other end of this.

    A resolver that has gone quiet fails slowly by default -- twelve seconds,
    measured against a real one -- and the device polls every thirteen. Bound
    the wait here rather than inheriting whatever the resolver felt like.
    """

    async def never_answers(hostname: str) -> list[str]:
        await asyncio.sleep(30)
        return ["206.80.104.221"]

    upstream = UpstreamAddress(
        url=VENDOR, resolve=never_answers, lookup_timeout=timedelta(seconds=0.05)
    )

    with pytest.raises(UpstreamUnavailable):
        await asyncio.wait_for(upstream.target(NOW), timeout=5)


async def test_a_hanging_resolver_falls_back_to_the_last_good_address():
    hang = False

    async def sometimes_hangs(hostname: str) -> list[str]:
        if hang:
            await asyncio.sleep(30)
        return ["206.80.104.221"]

    upstream = UpstreamAddress(
        url=VENDOR, resolve=sometimes_hangs, lookup_timeout=timedelta(seconds=0.05)
    )
    await upstream.target(NOW)
    hang = True

    target = await asyncio.wait_for(upstream.target(NOW + upstream.ttl), timeout=5)

    assert target.base_url == "http://206.80.104.221:8081"
