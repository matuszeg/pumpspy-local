"""Answering the device's own token request when the vendor cannot.

The device does not report into the void when the vendor stops answering.
After roughly nine minutes of failures it decides its token is stale, starts
asking for a new one, and stops sending telemetry until it gets one -- measured
on 2026-08-21, when the outage began at 22:14 and the first /oauth/token went
out at 22:23:05. During the longer 2026-08-20 outage /bbs_json, /bbs_parameters
and /new_firmware were absent for 87 minutes while /pings carried on.

One good token response ends it: at 22:27:26 on 2026-08-21 a single 200 was
followed within twelve seconds by /tm, /pings, /bbs_parameters and /bbs_json
all resuming. So answering the auth is sufficient, and nothing else has to be
faked.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from custom_components.pumpspy_local.core.auth import (
    AUTH_CONTENT_TYPE,
    DEVICE_REAUTH_INTERVAL_HOURS,
    LocalAuth,
    should_mint,
)

NOW = datetime(2026, 8, 21, 22, 23, 5, tzinfo=timezone.utc)


def test_a_healthy_vendor_is_never_second_guessed():
    """The one case that must never be papered over.

    The account the device authenticates as is not the owner's, so a password
    change on it is a real possibility. A 401 from a vendor that is answering
    is an answer, not an outage: it has to reach the device.
    """
    assert should_mint(vendor_reachable=True, relayed_status=401) is False


def test_an_isolated_failure_is_not_an_outage():
    """The vendor hangs up on roughly one request in ten even when it is fine.

    Those produce 502s on the token endpoint on ordinary days -- 2026-08-18
    22:12 and 2026-08-19 22:13 both recovered by themselves a couple of minutes
    later. Minting on a failed forward alone would fire during normal
    operation.
    """
    assert should_mint(vendor_reachable=True, relayed_status=None) is False
    assert should_mint(vendor_reachable=None, relayed_status=None) is False


def test_it_mints_once_the_vendor_is_judged_unreachable():
    """Both shapes the outages actually produced.

    2026-08-20 saw the token endpoint answer 401 while telemetry was failing
    wholesale, and it saw it fail outright. Either counts once the measured
    four-consecutive-failures verdict from #19 has already gone false.
    """
    assert should_mint(vendor_reachable=False, relayed_status=None) is True
    assert should_mint(vendor_reachable=False, relayed_status=401) is True
    assert should_mint(vendor_reachable=False, relayed_status=502) is True


def test_a_200_is_relayed_even_when_the_vendor_looks_down():
    """If the vendor answered the token request, its answer is the right one."""
    assert should_mint(vendor_reachable=False, relayed_status=200) is False


def test_the_minted_body_matches_the_captured_response():
    """Byte-for-byte the shape the device was seen to accept.

    The captured 200 is 147 bytes with these four keys in this order and no
    expires_in -- the device re-authenticates on its own clock, not on expiry.
    """
    body = LocalAuth().mint(NOW)

    assert len(body) == 147
    assert list(json.loads(body)) == [
        "access_token",
        "token_type",
        "refresh_token",
        "scope",
    ]
    minted = json.loads(body)
    assert minted["token_type"] == "bearer"
    assert minted["scope"] == "read"
    for key in ("access_token", "refresh_token"):
        value = minted[key]
        assert value == value.lower()
        assert UUID(value)


def test_each_mint_is_a_fresh_pair():
    auth = LocalAuth()
    first = json.loads(auth.mint(NOW))
    second = json.loads(auth.mint(NOW))

    assert first["access_token"] != second["access_token"]
    assert first["access_token"] != first["refresh_token"]


def test_minting_records_that_the_device_is_carrying_our_token():
    auth = LocalAuth()
    assert auth.issued is False

    auth.mint(NOW)

    assert auth.issued is True
    assert auth.issued_at == NOW


def test_a_real_token_clears_the_record():
    """Cleared when the vendor answers a token request itself.

    From then on the device is back on a token the vendor issued, and the
    sensor must not keep claiming otherwise.
    """
    auth = LocalAuth()
    auth.mint(NOW)

    auth.clear()

    assert auth.issued is False
    assert auth.issued_at is None


def test_the_content_type_is_the_one_the_vendor_sends():
    assert AUTH_CONTENT_TYPE == "application/json;charset=UTF-8"


def test_a_minted_token_survives_a_restart():
    """The sensor's whole job is to explain the rejections that follow a mint.

    Keeping it in the runtime alone meant a restart mid-outage left it reading
    off while the device was still carrying our token, denying responsibility
    at exactly the moment it exists to admit it.
    """
    auth = LocalAuth()
    auth.mint(NOW)

    restored = LocalAuth.from_stored(auth.to_stored(), now=NOW)

    assert restored.issued is True
    assert restored.issued_at == NOW


def test_the_token_itself_is_never_written_down():
    """Guardrail: the device's credential is relayed, never stored."""
    auth = LocalAuth()
    body = json.loads(auth.mint(NOW))

    stored = json.dumps(auth.to_stored())

    assert body["access_token"] not in stored
    assert body["refresh_token"] not in stored


def test_a_token_older_than_the_device_reauthenticates_is_not_restored():
    """The device replaces our token on its own four-hourly clock.

    If Home Assistant was down that long the device re-authenticated through
    the shim and the vendor gave it a real one, so claiming the rejections are
    ours would be inventing a signal rather than restoring one.
    """
    auth = LocalAuth()
    auth.mint(NOW)
    much_later = NOW + timedelta(hours=DEVICE_REAUTH_INTERVAL_HOURS, minutes=1)

    restored = LocalAuth.from_stored(auth.to_stored(), now=much_later)

    assert restored.issued is False
    assert restored.issued_at is None


def test_a_token_within_that_window_is_still_believed():
    auth = LocalAuth()
    auth.mint(NOW)
    shortly_after = NOW + timedelta(minutes=20)

    restored = LocalAuth.from_stored(auth.to_stored(), now=shortly_after)

    assert restored.issued is True


def test_nothing_stored_means_nothing_was_minted():
    """A first run, or an upgrade from a version that stored no such thing."""
    assert LocalAuth.from_stored({}, now=NOW).issued is False
    assert LocalAuth.from_stored(None, now=NOW).issued is False


def test_an_unreadable_timestamp_is_treated_as_no_token():
    """Storage written by hand, or by a future version. Never raise on it."""
    assert LocalAuth.from_stored({"issued_at": "not a date"}, now=NOW).issued is False


def test_a_cleared_token_stores_as_cleared():
    auth = LocalAuth()
    auth.mint(NOW)
    auth.clear()

    assert LocalAuth.from_stored(auth.to_stored(), now=NOW).issued is False
