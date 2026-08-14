"""Parsing the device's telemetry, against bytes it actually sent.

Every fixture here is a real captured body with the device id and access token
replaced. Nothing else about them has been touched, including the odd spacing.
"""

from pathlib import Path

from custom_components.pumpspy_local.core.parser import parse_bbs_json

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
