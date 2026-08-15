"""Per-device state.

The device sends only what changed, so state has to accumulate. This is the
layer that turns a stream of partial messages into something an entity can read.
"""

from pathlib import Path

from custom_components.pumpspy_local.core.parser import parse_bbs_json, parse_pings
from custom_components.pumpspy_local.core.state import DeviceState

FIXTURES = Path(__file__).parent / "fixtures"


def reading(name: str):
    return parse_bbs_json((FIXTURES / name).read_bytes())


def pings(name: str):
    return parse_pings((FIXTURES / name).read_bytes())


def test_applies_a_value_from_a_message():
    state = DeviceState(device_id="11111111111111")

    state.apply(reading("bbs_json_plain_battery.txt"))

    assert state.battery_volts == 13.324


def test_a_later_message_does_not_erase_earlier_values():
    """The whole reason this layer exists.

    A mains-power message contains only {"ac_power": 0}. If that were treated as
    a state snapshot it would wipe the battery voltage, and every other entity,
    every time the mains flickered.
    """
    state = DeviceState(device_id="11111111111111")

    state.apply(reading("bbs_json_plain_battery.txt"))
    state.apply(reading("bbs_json_ac_power.txt"))

    assert state.ac_power is False
    assert state.battery_volts == 13.324


def test_keeps_the_most_recent_pump_run():
    state = DeviceState(device_id="11111111111111")

    state.apply(reading("bbs_json_pump_run.txt"))

    assert state.last_run.pump == "primary"
    assert state.last_run.duration_seconds == 8.2


def test_a_later_message_does_not_erase_the_last_run():
    state = DeviceState(device_id="11111111111111")

    state.apply(reading("bbs_json_pump_run.txt"))
    state.apply(reading("bbs_json_high_water.txt"))

    assert state.high_water is True
    assert state.last_run is not None


def _run_reading(*, motor: int, mamp: int):
    """A pump-run message with a chosen motor and current."""
    import json

    from custom_components.pumpspy_local.core.parser import parse_bbs_json

    inner = json.dumps({"motor": motor, "time": 82, "mamp": mamp})
    return parse_bbs_json(
        json.dumps({"deviceid": 11111111111111, "json": inner}).encode()
    )


def test_the_fault_stays_raised_across_unrelated_messages():
    """The device sends motor_fail once and never sends a clearing message."""
    state = DeviceState(device_id="11111111111111")

    state.apply(reading("bbs_json_motor_fail.txt"))
    state.apply(reading("bbs_json_plain_battery.txt"))
    state.apply(reading("bbs_json_high_water.txt"))

    assert state.motor_fail is True


def test_a_healthy_primary_run_clears_the_fault():
    """What the vendor does server-side, and the only automatic clear we have."""
    state = DeviceState(device_id="11111111111111")
    state.apply(reading("bbs_json_motor_fail.txt"))

    state.apply(_run_reading(motor=1, mamp=2800))

    assert state.motor_fail is False


def test_a_backup_run_does_not_clear_the_fault():
    """The fault is about the primary pump. The backup running proves nothing.

    If anything it suggests the primary is not doing its job.
    """
    state = DeviceState(device_id="11111111111111")
    state.apply(reading("bbs_json_motor_fail.txt"))

    state.apply(_run_reading(motor=0, mamp=2800))

    assert state.motor_fail is True


def test_a_primary_run_drawing_no_current_does_not_clear_the_fault():
    """A run reporting zero current is the failure, not evidence against it."""
    state = DeviceState(device_id="11111111111111")
    state.apply(reading("bbs_json_motor_fail.txt"))

    state.apply(_run_reading(motor=1, mamp=0))

    assert state.motor_fail is True


def test_the_fault_can_be_cleared_by_hand():
    """The automatic rule is not trustworthy until the threshold is calibrated."""
    state = DeviceState(device_id="11111111111111")
    state.apply(reading("bbs_json_motor_fail.txt"))

    state.clear_fault()

    assert state.motor_fail is False


def test_stored_state_round_trips():
    state = DeviceState(device_id="11111111111111")
    state.apply(reading("bbs_json_motor_fail.txt"))
    state.apply(reading("bbs_json_ac_power.txt"))
    state.apply(reading("bbs_json_high_water.txt"))
    state.apply(reading("bbs_json_pump_run.txt"))
    state.motor_fail = True  # the pump run above cleared it; we want it stored raised

    restored = DeviceState.from_stored("11111111111111", state.to_stored())

    assert restored.motor_fail is True
    assert restored.ac_power is False
    assert restored.high_water is True
    assert restored.last_run.pump == "primary"
    assert restored.last_run.duration_seconds == 8.2


def test_periodic_readings_are_not_stored():
    """Voltages and signal strength arrive on their own schedule.

    Restoring them would show a stale number as though it were current, and the
    device will resend within a cycle anyway. Only what the device reports
    *on change* is worth keeping.
    """
    state = DeviceState(device_id="11111111111111")
    state.apply(reading("bbs_json_pump_run.txt"))
    state.apply_ping(pings("pings_rssi_type1.txt")[0])

    restored = DeviceState.from_stored("11111111111111", state.to_stored())

    assert restored.battery_volts is None
    assert restored.loaded_volts is None
    assert restored.wifi_dbm is None


def test_stored_state_survives_missing_keys():
    """An older stored payload must not break startup."""
    restored = DeviceState.from_stored("11111111111111", {})

    assert restored.device_id == "11111111111111"
    assert restored.motor_fail is None
    assert restored.last_run is None


def test_a_wifi_ping_sets_signal_strength():
    state = DeviceState(device_id="11111111111111")

    state.apply_ping(pings("pings_rssi_type1.txt")[0])

    assert state.wifi_dbm == -46.0


def test_an_unidentified_ping_type_is_ignored_rather_than_guessed_at():
    """Type 3 (~5.86) is unexplained. Assigning it a meaning would invent data."""
    state = DeviceState(device_id="11111111111111")

    state.apply_ping(pings("pings_type3.txt")[0])

    assert state.wifi_dbm is None
