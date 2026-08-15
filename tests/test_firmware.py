"""Deciding whether the vendor is offering the device a firmware update.

Grounded in one observed fact and no more: the "no update" reply is an empty
JSON array. No real update has ever been captured -- the largest response
payload of any kind in the captures is 168 bytes -- so everything else here is
deliberately conservative.
"""

from datetime import datetime, timedelta

from custom_components.pumpspy_local.core.firmware import (
    FirmwareChecker,
    Reply,
    Verdict,
    classify,
)


def test_the_captured_no_update_reply_is_recognised():
    """Chunked on the wire, so the decoded body is just the two bytes."""
    assert classify(200, b"[]") is Verdict.NO_UPDATE


def test_whitespace_around_the_empty_array_still_means_no_update():
    assert classify(200, b" [] \n") is Verdict.NO_UPDATE


def test_a_payload_that_is_not_the_known_reply_is_treated_as_an_update():
    """The only signal we can trust is "this is not the no-update reply".

    Erring towards calling it an update is the cheap mistake: the default mode
    only alerts, and letting a real update slip past unnoticed is the whole
    thing the feature exists to prevent.
    """
    assert classify(200, b'[{"version": "2.1.4"}]') is Verdict.UPDATE_OFFERED


def test_a_redirect_is_treated_as_an_update():
    """A 3xx is most likely pointing at the image itself."""
    assert classify(302, b"") is Verdict.UPDATE_OFFERED


def test_a_server_error_is_not_an_update():
    """Upstream having a bad day must not raise a firmware alert."""
    assert classify(500, b"Internal Server Error") is Verdict.UNKNOWN


def test_an_empty_body_is_not_taken_as_an_update():
    assert classify(200, b"") is Verdict.UNKNOWN


NOON = datetime(2026, 8, 14, 12, 0)
NO_UPDATE = Reply(200, b"[]")
AN_UPDATE = Reply(200, b'[{"version": "2.1.4"}]')


def test_the_first_check_has_to_ask_upstream():
    """Nothing cached yet, so there is nothing to answer the device with."""
    checker = FirmwareChecker()

    assert checker.should_query_upstream(NOON) is True


def test_a_second_check_moments_later_is_served_from_cache():
    """The device asks every ~13 seconds. Relaying all of that is pointless."""
    checker = FirmwareChecker()
    checker.record_upstream(NOON, NO_UPDATE, quarantine=False)

    assert checker.should_query_upstream(NOON + timedelta(seconds=13)) is False
    assert checker.reply_for_device() == NO_UPDATE


def test_upstream_is_asked_again_once_the_interval_has_passed():
    checker = FirmwareChecker(interval=timedelta(hours=24))
    checker.record_upstream(NOON, NO_UPDATE, quarantine=False)

    assert checker.should_query_upstream(NOON + timedelta(hours=25)) is True


def test_an_update_passes_straight_through_when_not_quarantining():
    """Observe mode must not alter what the device receives."""
    checker = FirmwareChecker()
    checker.record_upstream(NOON, NO_UPDATE, quarantine=False)

    served = checker.record_upstream(
        NOON + timedelta(hours=25), AN_UPDATE, quarantine=False
    )

    assert served == AN_UPDATE
    assert checker.held is None


def test_quarantine_withholds_the_update_and_answers_no_update():
    checker = FirmwareChecker()
    checker.record_upstream(NOON, NO_UPDATE, quarantine=True)

    served = checker.record_upstream(
        NOON + timedelta(hours=25), AN_UPDATE, quarantine=True
    )

    assert served == NO_UPDATE
    assert checker.held == AN_UPDATE


def test_a_held_update_stops_upstream_being_asked_again():
    """The precedence rule: held beats cache beats upstream.

    Without this a scheduled refresh would eventually hand the device the very
    update the user asked to hold.
    """
    checker = FirmwareChecker()
    checker.record_upstream(NOON, NO_UPDATE, quarantine=True)
    checker.record_upstream(NOON + timedelta(hours=25), AN_UPDATE, quarantine=True)

    assert checker.should_query_upstream(NOON + timedelta(days=30)) is False
    assert checker.reply_for_device() == NO_UPDATE


def test_approving_a_held_update_lets_it_through_next_time():
    checker = FirmwareChecker()
    checker.record_upstream(NOON, NO_UPDATE, quarantine=True)
    checker.record_upstream(NOON + timedelta(hours=25), AN_UPDATE, quarantine=True)

    checker.approve()

    assert checker.held is None
    assert checker.should_query_upstream(NOON + timedelta(days=30)) is True


def test_quarantine_cannot_engage_without_a_known_no_update_reply():
    """With no baseline there is nothing safe to answer with.

    Inventing one risks sending the device a reply shape it has never seen.
    """
    checker = FirmwareChecker()

    served = checker.record_upstream(NOON, AN_UPDATE, quarantine=True)

    assert served == AN_UPDATE
    assert checker.held is None
