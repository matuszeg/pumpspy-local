"""Parsing the device's telemetry, against bytes it actually sent.

Every fixture here is a real captured body with the device id and access token
replaced. Nothing else about them has been touched, including the odd spacing.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from custom_components.pumpspy_local.core.parser import (
    parse_bbs_json,
    parse_pings,
    parse_pump_alerts,
    parse_request,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parses_the_device_id():
    reading = parse_bbs_json(fixture("bbs_json_plain_battery.txt"))

    assert reading.device_id == "11111111111111"


def test_converts_battery_millivolts_to_volts():
    reading = parse_bbs_json(fixture("bbs_json_plain_battery.txt"))

    assert reading.battery_volts == 13.324


def test_absent_fields_are_none_because_messages_carry_only_what_changed():
    reading = parse_bbs_json(fixture("bbs_json_plain_battery.txt"))

    assert reading.ac_power is None
    assert reading.high_water is None
    assert reading.motor_fail is None


def test_reads_mains_power_lost():
    reading = parse_bbs_json(fixture("bbs_json_ac_power.txt"))

    assert reading.ac_power is False


def test_reads_the_high_water_alarm():
    reading = parse_bbs_json(fixture("bbs_json_high_water.txt"))

    assert reading.high_water is True


def test_reads_the_pump_fault():
    reading = parse_bbs_json(fixture("bbs_json_motor_fail.txt"))

    assert reading.motor_fail is True


def test_a_message_without_a_run_has_no_pump_run():
    reading = parse_bbs_json(fixture("bbs_json_plain_battery.txt"))

    assert reading.pump_run is None


def test_reads_which_pump_ran():
    reading = parse_bbs_json(fixture("bbs_json_pump_run.txt"))

    assert reading.pump_run.pump == "primary"


def test_run_duration_is_tenths_of_a_second_on_the_wire():
    """The captured run reports time=82, which is 8.2 seconds and not 82."""
    reading = parse_bbs_json(fixture("bbs_json_pump_run.txt"))

    assert reading.pump_run.duration_seconds == 8.2


def test_reads_the_motor_current():
    reading = parse_bbs_json(fixture("bbs_json_pump_run.txt"))

    assert reading.pump_run.current_milliamps == 2800


def test_a_run_message_also_carries_resting_and_loaded_voltage():
    reading = parse_bbs_json(fixture("bbs_json_pump_run.txt"))

    assert reading.battery_volts == 13.309
    assert reading.loaded_volts == 12.688


def test_pings_bodies_are_arrays_not_objects():
    """Unlike /bbs_json, a /pings body is a JSON array of entries."""
    pings = parse_pings(fixture("pings_rssi_type1.txt"))

    assert len(pings) == 1
    assert pings[0].device_id == "11111111111111"


def test_reads_wifi_signal_strength():
    pings = parse_pings(fixture("pings_rssi_type1.txt"))

    assert pings[0].data_type == 1
    assert pings[0].value == -46.0


def test_reads_the_unidentified_type_3_ping_without_interpreting_it():
    """Type 3 is unexplained (~5.86, plausibly motor amps).

    The parser should carry it through rather than guess at its meaning or
    drop it on the floor.
    """
    pings = parse_pings(fixture("pings_type3.txt"))

    assert pings[0].data_type == 3
    assert pings[0].value == 5.86


def test_pump_alerts_use_a_camelcase_schema_of_their_own():
    """This endpoint spells the device id "deviceID", unlike every other one.

    Reading it with the usual lowercase key would silently yield nothing.
    """
    alerts = parse_pump_alerts(fixture("pumpalert_idPumpAlertType.txt"))

    assert alerts[0].device_id == "11111111111111"
    assert alerts[0].alert_type == 105
    assert alerts[0].value == 0


def _bbs_body(**inner) -> bytes:
    return json.dumps(
        {"deviceid": 11111111111111, "utcunixtime": 1786652606000, "json": json.dumps(inner)}
    ).encode()


def test_a_field_we_do_not_know_about_does_not_break_the_rest():
    """A firmware update that adds a field must not cost us the reading."""
    reading = parse_bbs_json(_bbs_body(battery_voltage=13324, brand_new_field=7))

    assert reading.battery_volts == 13.324


def test_unknown_fields_are_logged_so_a_firmware_change_gets_noticed(caplog):
    parse_bbs_json(_bbs_body(battery_voltage=13324, brand_new_field=7))

    assert "brand_new_field" in caplog.text


def test_parse_request_returns_none_rather_than_raising_on_junk():
    """The parser runs beside forwarding; it must never take the request down."""
    assert parse_request("/bbs_json", b"this is not json") is None


def test_parse_request_returns_none_when_a_field_it_needs_is_missing():
    assert parse_request("/bbs_json", b'{"nothing": "useful"}') is None


def test_parse_request_ignores_paths_it_does_not_handle():
    assert parse_request("/tm", b"") is None


def test_parse_request_routes_a_known_path():
    reading = parse_request("/bbs_json", fixture("bbs_json_plain_battery.txt"))

    assert reading.battery_volts == 13.324


def test_reads_the_clock_the_device_stamped_the_message_with():
    """Kept because every timestamp we hold otherwise is arrival time.

    A device that queues an event during an outage and delivers it on reconnect
    would be indistinguishable from one that ran the pump at that moment, and
    run timing is what #17 wants to build on.
    """
    reading = parse_bbs_json(fixture("bbs_json_plain_battery.txt"))

    assert reading.device_time == datetime(
        2026, 8, 13, 20, 23, 26, tzinfo=timezone.utc
    )


def test_the_device_clock_is_milliseconds_on_the_wire():
    body = json.dumps(
        {"deviceid": 11111111111111, "utcunixtime": 1786652606000, "json": "{}"}
    ).encode()

    assert parse_bbs_json(body).device_time == datetime(
        2026, 8, 13, 20, 23, 26, tzinfo=timezone.utc
    )


def test_a_message_without_a_device_clock_still_parses():
    """Nothing may hinge on a field the firmware could stop sending."""
    body = json.dumps({"deviceid": 11111111111111, "json": "{}"}).encode()

    assert parse_bbs_json(body).device_time is None


def test_an_unreadable_device_clock_does_not_lose_the_reading():
    """The voltage in the same message is worth more than the timestamp."""
    body = json.dumps(
        {
            "deviceid": 11111111111111,
            "utcunixtime": "not a number",
            "json": '{"battery_voltage": 13324}',
        }
    ).encode()
    reading = parse_bbs_json(body)

    assert reading.device_time is None
    assert reading.battery_volts == 13.324


def test_a_non_finite_device_clock_does_not_cost_us_the_whole_message():
    """The device sometimes writes its clock out as a C non-finite float.

    Every parse failure recorded on the live install was this, byte for byte:
    ``utcunixtime`` rendered as ``1.#INF00``, which is what printf("%f") emits
    for infinity. It is not valid JSON, so the decoder abandons the message and
    the reading beside it goes with it -- and for /pings that reading is the
    only thing in there we actually read.
    """
    pings = parse_request("/pings", fixture("pings_rssi_nonfinite_clock.txt"))

    assert pings is not None
    assert pings[0].value == -46.0
    assert pings[0].data_type == 1


def test_the_other_non_finite_spellings_are_tolerated_too():
    """The same printf writes NaN and indefinite differently again."""
    for spelling in (b"1.#QNAN0", b"-1.#IND00", b"1.#INF00"):
        body = b'[{"deviceid": 11111111111111, "utcunixtime": %s,' % spelling
        body += b'"idpings_data_type": 1, "value": -46.000000 }]'

        pings = parse_request("/pings", body)

        assert pings is not None, spelling
        assert pings[0].value == -46.0


def test_a_non_finite_clock_leaves_the_timestamp_unknown_not_wrong():
    body = (
        b'{"deviceid": 11111111111111, "utcunixtime": 1.#INF00, '
        b'"json": "{\\"battery_voltage\\": 13324}"}'
    )
    reading = parse_request("/bbs_json", body)

    assert reading is not None
    assert reading.device_time is None
    assert reading.battery_volts == 13.324


def test_a_non_finite_clock_inside_the_escaped_inner_json_is_tolerated():
    """/bbs_json hides a second JSON document in a string, and it can be hit too.

    Those characters need no escaping, so the malformation appears verbatim in
    the raw body and a repair over the raw bytes reaches it.
    """
    body = (
        b'{"deviceid": 11111111111111, '
        b'"json": "{\\"battery_voltage\\": 13324, \\"mamp\\": 1.#INF00, '
        b'\\"motor\\": 1, \\"time\\": 100}"}'
    )
    reading = parse_request("/bbs_json", body)

    assert reading is not None
    assert reading.battery_volts == 13.324
    assert reading.pump_run is not None
    assert reading.pump_run.current_milliamps is None


def test_a_repaired_message_is_not_shouted_about(caplog):
    """892 warnings with a traceback each was the other half of the complaint."""
    with caplog.at_level("DEBUG"):
        parse_request("/pings", fixture("pings_rssi_nonfinite_clock.txt"))

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_a_body_that_is_genuinely_not_json_still_warns(caplog):
    """The repair must not turn a real problem into a silent one."""
    assert parse_request("/pings", b"<html>nope</html>") is None
    assert "could not parse" in caplog.text
