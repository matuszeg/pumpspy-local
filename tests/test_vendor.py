"""Judging whether the vendor is reachable from how forwarding is going.

Every threshold here is measured, not chosen. Counting runs of consecutive
failed forwards in the shim's access log:

- Healthy traffic, ~1100 forwarded requests over 22 hours: the longest run of
  failures is **2**, and it happened once. The vendor drops the odd request
  even on a good day.
- The 2026-08-20 outage: runs of **4 to 22**, typically 7 or 8, and scattered
  through them are *isolated single successes* -- the vendor answered one
  request, then went back to failing.

So four consecutive failures separates a real outage from a bad day, and
recovery needs two consecutive successes, because a single one happened
repeatedly while the vendor was still down.
"""

from datetime import datetime, timedelta

from custom_components.pumpspy_local.core.vendor import VendorHealth

NOON = datetime(2026, 8, 20, 12, 0, 0)


def _minutes(count: int) -> datetime:
    return NOON + timedelta(minutes=count)


def test_reachability_is_unknown_until_something_has_been_forwarded():
    """Nothing has been tried, so nothing is known.

    Distinct from "the vendor is fine": an alert that cannot tell those apart
    is the thing this exists to fix.
    """
    assert VendorHealth().reachable is None


def test_one_delivery_is_enough_to_call_the_vendor_reachable():
    health = VendorHealth()
    health.record_success(NOON)
    assert health.reachable is True


def test_an_isolated_failure_does_not_mean_the_vendor_is_down():
    """The vendor hangs up on roughly one request in ten, all day, healthily."""
    health = VendorHealth()
    health.record_success(NOON)
    health.record_failure("Server disconnected")
    assert health.reachable is True


def test_two_consecutive_failures_still_does_not():
    """Two in a row is the worst a healthy day produced in 22 hours of log."""
    health = VendorHealth()
    health.record_success(NOON)
    health.record_failure("Server disconnected")
    health.record_failure("Server disconnected")
    assert health.reachable is True


def test_four_consecutive_failures_means_the_vendor_is_down():
    """Four is above anything a healthy day did and below the outage's runs."""
    health = VendorHealth()
    health.record_success(NOON)
    for _ in range(4):
        health.record_failure("Server disconnected")
    assert health.reachable is False


def test_the_vendor_can_be_declared_down_before_it_was_ever_reached():
    """A restart during an outage starts from unknown and must still say so."""
    health = VendorHealth()
    for _ in range(4):
        health.record_failure("Server disconnected")
    assert health.reachable is False


def test_a_run_of_failures_is_forgotten_once_one_gets_through():
    health = VendorHealth()
    health.record_success(NOON)
    for _ in range(3):
        health.record_failure("Server disconnected")
    health.record_success(_minutes(4))
    for _ in range(3):
        health.record_failure("Server disconnected")
    assert health.reachable is True


def test_a_single_delivery_during_an_outage_is_not_a_recovery():
    """Seen repeatedly on 2026-08-20: one request answered, then failing again."""
    health = VendorHealth()
    for _ in range(4):
        health.record_failure("Server disconnected")
    health.record_success(_minutes(5))
    assert health.reachable is False


def test_two_consecutive_deliveries_are_a_recovery():
    health = VendorHealth()
    for _ in range(4):
        health.record_failure("Server disconnected")
    health.record_success(_minutes(5))
    health.record_success(_minutes(6))
    assert health.reachable is True


def test_the_last_delivery_is_when_a_forward_last_got_through():
    """The number the notification wants: how long the vendor has been blind."""
    health = VendorHealth()
    health.record_success(NOON)
    health.record_failure("Server disconnected")
    assert health.last_delivery == NOON


def test_nothing_has_been_delivered_before_the_first_success():
    health = VendorHealth()
    health.record_failure("Server disconnected")
    assert health.last_delivery is None


def test_the_most_recent_failure_keeps_its_reason():
    """"Server disconnected" and "Connection reset by peer" were both seen."""
    health = VendorHealth()
    health.record_failure("Server disconnected")
    health.record_failure("[Errno 104] Connection reset by peer")
    assert health.last_error == "[Errno 104] Connection reset by peer"


def test_the_length_of_the_current_failure_run_is_visible():
    """Reported alongside the sensor so a near-miss can be seen, not guessed."""
    health = VendorHealth()
    health.record_failure("Server disconnected")
    health.record_failure("Server disconnected")
    assert health.consecutive_failures == 2
    health.record_success(_minutes(2))
    assert health.consecutive_failures == 0
