"""Nothing the device sends is allowed to vanish without a word.

Three message types sat in the captures for months, parsed or not, and none of
them ever reached a log line on a running install (#13). The rule here is that
the *first* sighting of anything unfamiliar is a warning and every one after it
is a debug line: loud enough to survive a live instance that logs this
integration at WARNING, quiet enough that a message arriving daily does not
turn into a daily nag.
"""

import logging

import pytest

from custom_components.pumpspy_local.core.novelty import Novelties
from custom_components.pumpspy_local.core.parser import (
    BbsReading,
    Ping,
    PumpAlert,
)


@pytest.fixture(name="novelties")
def novelties_fixture() -> Novelties:
    return Novelties()


def warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_an_unfamiliar_path_is_warned_about_once(novelties, caplog):
    caplog.set_level(logging.DEBUG)

    novelties.note("/pump_outlet_alerts_v2", None)
    novelties.note("/pump_outlet_alerts_v2", None)

    assert len(warnings(caplog)) == 1
    assert "/pump_outlet_alerts_v2" in warnings(caplog)[0]


@pytest.mark.parametrize(
    "path",
    ["/tm", "/oauth/token", "/new_firmware/11111111111111", "/bbs_parameters/11111111111111"],
)
def test_the_paths_we_know_we_do_not_parse_stay_quiet(novelties, caplog, path):
    """These are mapped, not mysterious.

    ``/tm`` is the device asking the vendor what time it is, and the other
    three are answered rather than read. Warning about them would fire on every
    healthy install and say nothing.
    """
    caplog.set_level(logging.DEBUG)

    novelties.note(path, None)

    assert warnings(caplog) == []


def test_a_body_that_failed_to_parse_is_not_reported_as_unread(novelties, caplog):
    """The parser already warned about it, and it is not an unknown endpoint.

    ``parse_request`` returns None both for a path it has no parser for and for
    a body that would not parse, so telling those apart matters: saying nothing
    here reads /bbs_json would be false and would arrive on top of a warning
    that had already been raised.
    """
    caplog.set_level(logging.DEBUG)

    novelties.note("/bbs_json", None)

    assert warnings(caplog) == []


def test_the_device_id_is_not_written_into_the_log(novelties, caplog):
    """A per-device path would otherwise warn once per device, id and all."""
    caplog.set_level(logging.DEBUG)

    novelties.note("/something_new/11111111111111", None)
    novelties.note("/something_new/22222222222222", None)

    assert len(warnings(caplog)) == 1
    assert "11111111111111" not in warnings(caplog)[0]


def test_a_pump_alert_is_reported_with_its_type_and_value(novelties, caplog):
    caplog.set_level(logging.DEBUG)

    alert = PumpAlert(
        device_id="11111111111111", alert_type=105, record_number=0, value=0.0
    )
    novelties.note("/pump_outlet_alerts", [alert])
    novelties.note("/pump_outlet_alerts", [alert])

    assert len(warnings(caplog)) == 1
    assert "105" in warnings(caplog)[0]


def test_each_new_alert_type_gets_its_own_first_warning(novelties, caplog):
    caplog.set_level(logging.DEBUG)

    for alert_type in (105, 106):
        novelties.note(
            "/pump_outlet_alerts",
            [
                PumpAlert(
                    device_id="11111111111111",
                    alert_type=alert_type,
                    record_number=0,
                    value=1.0,
                )
            ],
        )

    assert len(warnings(caplog)) == 2


def test_wifi_pings_are_not_news(novelties, caplog):
    caplog.set_level(logging.DEBUG)

    novelties.note("/pings", [Ping(device_id="1", data_type=1, value=-58.0)])

    assert warnings(caplog) == []


def test_an_unread_ping_type_is_reported_once(novelties, caplog):
    """Type 3 is motor amps, and we deliberately do not keep it.

    It duplicates ``mamp`` from the run message, which is already recorded and
    already known to be untrustworthy. Being told it arrived is still worth one
    line, because the alternative is discovering a new type by not noticing it.
    """
    caplog.set_level(logging.DEBUG)

    for _ in range(2):
        novelties.note("/pings", [Ping(device_id="1", data_type=3, value=5.86)])

    assert len(warnings(caplog)) == 1
    assert "5.86" in warnings(caplog)[0]


def test_a_field_we_have_never_seen_in_bbs_json_is_reported_once(novelties, caplog):
    """The likeliest cause is a firmware change, which is worth hearing about."""
    caplog.set_level(logging.DEBUG)

    for _ in range(2):
        novelties.note(
            "/bbs_json",
            BbsReading(device_id="1", unknown_fields=("pit_depth",)),
        )

    assert len(warnings(caplog)) == 1
    assert "pit_depth" in warnings(caplog)[0]


def test_an_ordinary_reading_says_nothing(novelties, caplog):
    caplog.set_level(logging.DEBUG)

    novelties.note("/bbs_json", BbsReading(device_id="1", battery_volts=13.3))

    assert warnings(caplog) == []
