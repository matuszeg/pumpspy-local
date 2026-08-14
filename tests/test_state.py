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


def test_a_wifi_ping_sets_signal_strength():
    state = DeviceState(device_id="11111111111111")

    state.apply_ping(pings("pings_rssi_type1.txt")[0])

    assert state.wifi_dbm == -46.0


def test_an_unidentified_ping_type_is_ignored_rather_than_guessed_at():
    """Type 3 (~5.86) is unexplained. Assigning it a meaning would invent data."""
    state = DeviceState(device_id="11111111111111")

    state.apply_ping(pings("pings_type3.txt")[0])

    assert state.wifi_dbm is None
