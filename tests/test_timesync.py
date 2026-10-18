"""Answering the device's clock sync when the vendor cannot.

A minted token (#21) does not keep the device reporting through an outage on
its own. Measured 2026-10-06 to 10-18: in every evening outage the device went
/oauth/token (minted) -> GET /tm (502) -> /oauth/token again two minutes later,
83 mints in 13 days, and the loop only ended when the vendor answered /tm.
"""

import json
from datetime import datetime, timezone

from custom_components.pumpspy_local.core.timesync import (
    TIME_CONTENT_TYPE,
    should_answer_time,
    time_response,
)

# The captured reply: Date: Thu, 13 Aug 2026 22:25:41 GMT, body
# {"utctime":1786659941836}.
CAPTURED_AT = datetime(2026, 8, 13, 22, 25, 41, 836000, tzinfo=timezone.utc)


def test_the_body_matches_the_captured_reply_byte_for_byte():
    assert time_response(CAPTURED_AT) == b'{"utctime":1786659941836}'


def test_the_body_is_utc_milliseconds_whatever_the_zone_it_was_asked_in():
    """HA hands out aware datetimes; the device only understands UTC epoch ms."""
    local = CAPTURED_AT.astimezone(timezone.utc).astimezone()
    assert json.loads(time_response(local)) == {"utctime": 1786659941836}


def test_the_content_type_matches_the_vendor():
    assert TIME_CONTENT_TYPE == "application/json;charset=UTF-8"


def test_a_healthy_vendor_answers_its_own_clock_sync():
    """Its answer passes through untouched, failed or not."""
    assert should_answer_time(vendor_reachable=True, relayed_status=None) is False
    assert should_answer_time(vendor_reachable=True, relayed_status=500) is False


def test_an_unsettled_verdict_is_not_an_outage():
    assert should_answer_time(vendor_reachable=None, relayed_status=None) is False


def test_it_answers_once_the_vendor_is_judged_unreachable():
    assert should_answer_time(vendor_reachable=False, relayed_status=None) is True
    assert should_answer_time(vendor_reachable=False, relayed_status=502) is True


def test_a_vendor_that_does_answer_the_clock_is_believed():
    """The real clock is always better than ours, even mid-outage."""
    assert should_answer_time(vendor_reachable=False, relayed_status=200) is False
