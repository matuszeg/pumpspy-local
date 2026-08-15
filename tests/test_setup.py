"""The Home Assistant layer: does the listener actually bind and relay?

Proven by hand against a real HA container first. These pin it down so the
config flow and entity platforms can be built on top without re-testing by hand.
"""

import asyncio
import socket
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from aiohttp import ClientSession
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pumpspy_local.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ during tests."""
    yield


@pytest.fixture
def expected_lingering_tasks() -> bool:
    """Downgrade the harness's lingering-task failure to a warning, here only.

    Replaying a GET that declares a body it never sends leaves aiohttp's
    connection handler alive until the peer disconnects, and it outlives the
    test by a beat. It is an artefact of driving the listener over a raw socket,
    not a leak in the integration: the runner is cleaned up in
    ``shutdown_listener`` below, and a real device sends Connection: close.

    Tradeoff worth knowing: while this is in place, a genuine task leak in this
    module would warn instead of fail.
    """
    return True


@pytest_asyncio.fixture(autouse=True)
async def shutdown_listener(hass):
    """Stop the listener between tests.

    Otherwise its connection handlers outlive the test and Home Assistant's
    harness rightly fails them as lingering tasks.
    """
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)


async def _setup(hass, upstream, port) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"port": port, "upstream": f"http://{upstream.host}:{upstream.port}"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def runtime_of(hass, entry) -> object:
    return hass.data[DOMAIN][entry.entry_id]


async def test_setup_binds_the_port_and_relays_to_upstream(hass, upstream, free_port):
    await _setup(hass, upstream, free_port)

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{free_port}/bbs_json", data=b'{"deviceid":"X"}'
        ) as response:
            body = await response.read()

    assert upstream.received["method"] == "POST"
    assert upstream.received["path"] == "/bbs_json"
    assert upstream.received["body"] == b'{"deviceid":"X"}'
    assert body == b"ok"


async def test_setup_rewrites_the_host_header_for_upstream(hass, upstream, free_port):
    """The device's Host header names us, not the vendor.

    Relaying it verbatim would announce a local address to the vendor, which is
    the kind of thing that gets a request rejected for no obvious reason.
    """
    await _setup(hass, upstream, free_port)

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/pings", data=b"{}"):
            pass

    assert upstream.received["headers"]["Host"] == f"{upstream.host}:{upstream.port}"


async def test_get_with_a_stale_content_length_does_not_hang(hass, upstream, free_port):
    """Real captures show the device sending Content-Length on GETs, with no body.

    Waiting for a body that never arrives wedges the request until the device
    gives up. It polls /new_firmware roughly every 13 seconds, so this is the
    hottest path there is: getting it wrong stalls the firmware check forever.

    Byte shape is taken from a real capture, with the device id replaced.
    """
    await _setup(hass, upstream, free_port)

    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    writer.write(
        b"GET /new_firmware/11111111111111 HTTP/1.1\r\n"
        b"Host: www.pumpspy.com:8081 \r\n"
        b"Content-Type: application/json;charset=UTF-8 \r\n"
        b"Content-Length: 148 \r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    await writer.drain()

    try:
        reply = await asyncio.wait_for(reader.read(1024), timeout=5)
    finally:
        writer.close()
        await writer.wait_closed()

    assert reply.startswith(b"HTTP/1.1 200"), reply[:120]
    assert upstream.received["path"] == "/new_firmware/11111111111111"


async def test_double_space_in_the_request_line_is_accepted(hass, upstream, free_port):
    """The device emits "POST  /bbs_json" with two spaces. Captured, verbatim.

    Stricter parsers reject this outright, so it is worth a test that will shout
    if the listener is ever swapped for one that does.
    """
    await _setup(hass, upstream, free_port)

    reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
    writer.write(
        b"POST  /bbs_json HTTP/1.1\r\n"
        b"Host: www.pumpspy.com:8081 \r\n"
        b"Content-Length: 2 \r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"{}"
    )
    await writer.drain()

    try:
        reply = await asyncio.wait_for(reader.read(1024), timeout=5)
    finally:
        writer.close()
        await writer.wait_closed()

    assert reply.startswith(b"HTTP/1.1 200"), reply[:120]
    assert upstream.received["path"] == "/bbs_json"


async def test_a_posted_reading_becomes_device_state(hass, upstream, free_port):
    """The end-to-end path: bytes on the wire become something an entity can read."""
    entry = await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    device = runtime_of(hass, entry).devices["11111111111111"]
    assert device.battery_volts == 13.324


async def test_a_posted_ping_becomes_wifi_signal(hass, upstream, free_port):
    """A /pings body is an array, and a device can first appear through one."""
    entry = await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "pings_rssi_type1.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/pings", data=body):
            pass
    await hass.async_block_till_done()

    device = runtime_of(hass, entry).devices["11111111111111"]
    assert device.wifi_dbm == -46.0


async def test_a_parse_failure_does_not_break_forwarding(hass, upstream, free_port):
    """Parsing runs beside delivery to the vendor. It must never cost a delivery."""
    entry = await _setup(hass, upstream, free_port)

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{free_port}/bbs_json", data=b"utter nonsense"
        ) as response:
            assert response.status == 200
    await hass.async_block_till_done()

    assert upstream.received["body"] == b"utter nonsense"
    assert runtime_of(hass, entry).devices == {}


async def test_a_reading_creates_a_battery_voltage_entity(hass, upstream, free_port):
    """Devices are not configured; they are adopted when they first report."""
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    sensors = [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("battery_voltage")
    ]
    assert len(sensors) == 1
    assert sensors[0].state == "13.324"


async def test_entities_are_grouped_under_a_device(hass, upstream, free_port):
    """Home Assistant only registers devices for entities on a config entry.

    Under YAML setup the DeviceInfo was silently ignored and the sensors sat
    loose in the entity list, which is why setup moved to a config entry.
    """
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, "11111111111111")}
    )
    assert device is not None
    assert device.manufacturer == "Richtech"
    assert device.model == "PumpSpy / PitBoss+"


async def test_the_whole_entity_set_appears_with_the_right_values(
    hass, upstream, free_port
):
    """Feed one of each captured message and check what a user would see."""
    await _setup(hass, upstream, free_port)
    fixtures = Path(__file__).parent / "fixtures"
    messages = [
        ("/bbs_json", "bbs_json_pump_run.txt"),
        ("/bbs_json", "bbs_json_ac_power.txt"),
        ("/bbs_json", "bbs_json_high_water.txt"),
        ("/bbs_json", "bbs_json_motor_fail.txt"),
        ("/pings", "pings_rssi_type1.txt"),
    ]

    async with ClientSession() as session:
        for path, name in messages:
            async with session.post(
                f"http://127.0.0.1:{free_port}{path}",
                data=(fixtures / name).read_bytes(),
            ):
                pass
    await hass.async_block_till_done()

    def state_ending(suffix: str, domain: str = "sensor") -> str:
        matches = [
            state
            for state in hass.states.async_all(domain)
            if state.entity_id.endswith(suffix)
        ]
        assert len(matches) == 1, f"{suffix}: {[m.entity_id for m in matches]}"
        return matches[0].state

    # The pump run carried both voltages; later messages must not have erased them.
    assert state_ending("battery_voltage") == "13.309"
    assert state_ending("battery_voltage_under_load") == "12.688"
    assert state_ending("wifi_signal") == "-46.0"

    assert state_ending("last_pump") == "primary"
    assert state_ending("last_run_duration") == "8.2"
    assert state_ending("last_run_current") == "2800"

    binary = "binary_sensor"
    assert state_ending("mains_power", binary) == "off"  # ac_power 0 = mains lost
    assert state_ending("high_water", binary) == "on"
    assert state_ending("pump_failure", binary) == "on"


async def test_a_pump_run_fires_an_event(hass, upstream, free_port):
    """A run is a discrete occurrence, not a state.

    Automations should be able to trigger on "the pump ran" rather than watch a
    sensor and infer it from a value changing.
    """
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_pump_run.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    events = hass.states.async_all("event")
    assert len(events) == 1
    assert events[0].attributes["event_type"] == "primary"
    assert events[0].attributes["duration_seconds"] == 8.2
    assert events[0].attributes["current_milliamps"] == 2800


async def test_a_run_after_the_device_is_known_also_fires(hass, upstream, free_port):
    """The live path, as opposed to a run that arrives before the entity exists.

    Here the device is already known from an earlier message, so the event
    entity is listening when the run comes in.
    """
    await _setup(hass, upstream, free_port)
    fixtures = Path(__file__).parent / "fixtures"

    async with ClientSession() as session:
        for name in ("bbs_json_plain_battery.txt", "bbs_json_pump_run.txt"):
            async with session.post(
                f"http://127.0.0.1:{free_port}/bbs_json",
                data=(fixtures / name).read_bytes(),
            ):
                pass
            await hass.async_block_till_done()

    events = hass.states.async_all("event")
    assert len(events) == 1
    assert events[0].attributes["event_type"] == "primary"
    assert events[0].attributes["duration_seconds"] == 8.2


async def test_a_message_without_a_run_fires_no_event(hass, upstream, free_port):
    """Battery and alarm messages must not look like pump runs."""
    await _setup(hass, upstream, free_port)
    body = (
        Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt"
    ).read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    events = hass.states.async_all("event")
    assert len(events) == 1  # the entity exists...
    assert events[0].state in (None, "unknown")  # ...but has never fired


async def test_alarm_state_survives_a_restart(hass, upstream, free_port):
    """The device sends these only when they change.

    Without persistence, a restart leaves "mains power" reading unknown until
    the next actual power cut, which could be months. An alarm whose resting
    state is unknown cannot be alerted on.
    """
    entry = await _setup(hass, upstream, free_port)
    fixtures = Path(__file__).parent / "fixtures"

    async with ClientSession() as session:
        for name in ("bbs_json_motor_fail.txt", "bbs_json_ac_power.txt"):
            async with session.post(
                f"http://127.0.0.1:{free_port}/bbs_json",
                data=(fixtures / name).read_bytes(),
            ):
                pass
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device = runtime_of(hass, entry).devices["11111111111111"]
    assert device.motor_fail is True
    assert device.ac_power is False

    fault = [
        state
        for state in hass.states.async_all("binary_sensor")
        if state.entity_id.endswith("pump_failure")
    ]
    assert fault[0].state == "on"


async def test_the_fault_can_be_cleared_from_home_assistant(hass, upstream, free_port):
    """The automatic clear needs a current threshold nobody has calibrated.

    Until it is trustworthy the user needs an unambiguous way out that does not
    involve waiting for the pump to run.
    """
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_motor_fail.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    def fault_state() -> str:
        return next(
            state.state
            for state in hass.states.async_all("binary_sensor")
            if state.entity_id.endswith("pump_failure")
        )

    assert fault_state() == "on"

    button = next(
        state.entity_id
        for state in hass.states.async_all("button")
        if state.entity_id.endswith("clear_pump_failure")
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    await hass.async_block_till_done()

    assert fault_state() == "off"


async def test_run_and_gallon_totals_appear(hass, upstream, free_port):
    """Two runs of the captured 8.2s cycle, at the default one gallon per second."""
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_pump_run.txt").read_bytes()

    async with ClientSession() as session:
        for _ in range(2):
            async with session.post(
                f"http://127.0.0.1:{free_port}/bbs_json", data=body
            ):
                pass
    await hass.async_block_till_done()

    def state_ending(suffix: str) -> str:
        matches = [
            state
            for state in hass.states.async_all("sensor")
            if state.entity_id.endswith(suffix)
        ]
        assert len(matches) == 1, f"{suffix}: {[m.entity_id for m in matches]}"
        return matches[0].state

    assert state_ending("last_run_estimated_gallons") == "8"
    assert state_ending("primary_runs_today") == "2"
    assert state_ending("primary_estimated_gallons_today") == "16"
    assert state_ending("primary_runs_total") == "2"

    # The backup pump did not run, and must not be lumped in with the primary.
    assert state_ending("backup_runs_today") == "0"
    assert state_ending("backup_estimated_gallons_today") == "0"


async def test_the_configured_flow_rate_changes_the_estimate(
    hass, upstream, free_port
):
    """One gallon per second is nominal, not measured for a given install."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "port": free_port,
            "upstream": f"http://{upstream.host}:{upstream.port}",
            "flow_rate": 0.5,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    body = (Path(__file__).parent / "fixtures" / "bbs_json_pump_run.txt").read_bytes()
    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    gallons = next(
        state.state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("last_run_estimated_gallons")
    )
    assert gallons == "4"  # 8.2s at half a gallon per second, rounded down


async def test_repeated_firmware_checks_do_not_all_reach_the_vendor(
    hass, upstream, free_port
):
    """The device asks every ~13 seconds. One answer serves them all.

    The device must still get a reply every time -- from its point of view
    nothing has changed.
    """
    upstream.reply["body"] = b"[]"  # the captured "no update" reply
    await _setup(hass, upstream, free_port)

    async with ClientSession() as session:
        for _ in range(3):
            async with session.get(
                f"http://127.0.0.1:{free_port}/new_firmware/11111111111111"
            ) as response:
                assert response.status == 200
                assert await response.read() == b"[]"
    await hass.async_block_till_done()

    firmware_calls = [p for p in upstream.requests if p.startswith("/new_firmware")]
    assert len(firmware_calls) == 1


async def test_telemetry_is_never_throttled(hass, upstream, free_port):
    """Only the firmware check is cached. Readings must always go through."""
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_pump_run.txt").read_bytes()

    async with ClientSession() as session:
        for _ in range(3):
            async with session.post(
                f"http://127.0.0.1:{free_port}/bbs_json", data=body
            ):
                pass
    await hass.async_block_till_done()

    assert len([p for p in upstream.requests if p == "/bbs_json"]) == 3


async def test_an_offered_update_shows_up_in_home_assistant(
    hass, upstream, free_port
):
    """Observe mode does not withhold the update, but must still say so."""
    await _setup(hass, upstream, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt").read_bytes()

    async with ClientSession() as session:
        # The device has to be known before it can have entities.
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
        await hass.async_block_till_done()

        upstream.reply["body"] = b'[{"version": "2.1.4"}]'
        async with session.get(
            f"http://127.0.0.1:{free_port}/new_firmware/11111111111111"
        ) as response:
            # Observe mode relays it untouched.
            assert await response.read() == b'[{"version": "2.1.4"}]'
    await hass.async_block_till_done()

    detected = [
        state
        for state in hass.states.async_all("binary_sensor")
        if state.entity_id.endswith("firmware_update")
    ]
    assert len(detected) == 1
    assert detected[0].state == "on"


async def test_unloading_releases_the_port(hass, upstream, free_port):
    """A reconfigure or reload must not leave the port held."""
    entry = await _setup(hass, upstream, free_port)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", free_port))  # raises if the listener still holds it


# Nothing listens on port 1, so forwarding there fails immediately and
# deterministically -- no waiting on a timeout.
DEAD_UPSTREAM = "http://127.0.0.1:1"


async def _setup_pointing_at(hass, url, port) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"port": port, "upstream": url})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_the_device_is_told_when_the_vendor_cannot_be_reached(hass, free_port):
    """Never a synthetic 200.

    A fabricated success tells the device its event was delivered, and the
    device then has no reason to retry -- the event is lost for good.
    """
    await _setup_pointing_at(hass, DEAD_UPSTREAM, free_port)

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{free_port}/bbs_json", data=b"{}"
        ) as response:
            assert response.status == 502


async def test_telemetry_still_lands_locally_when_the_vendor_is_unreachable(
    hass, free_port
):
    """Local monitoring is the whole point; it cannot depend on the vendor.

    The device's message is in our hands either way, and the vendor being down
    -- or the upstream being misconfigured -- is no reason to throw it away.
    """
    entry = await _setup_pointing_at(hass, DEAD_UPSTREAM, free_port)
    body = (Path(__file__).parent / "fixtures" / "bbs_json_plain_battery.txt").read_bytes()

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{free_port}/bbs_json", data=body):
            pass
    await hass.async_block_till_done()

    device = runtime_of(hass, entry).devices["11111111111111"]
    assert device.battery_volts == 13.324


async def test_a_redirect_that_points_back_at_us_is_refused(hass, free_port, caplog):
    """The failure this whole mechanism exists to prevent.

    With the rewrite in place, the vendor's name answers with the Home Assistant
    host for anyone who asks -- including Home Assistant. Forwarding to that
    answer is forwarding into our own listener, over and over.
    """

    async def resolves_to_us(hostname: str) -> list[str]:
        return ["192.168.7.57"]

    with patch(
        "custom_components.pumpspy_local.aiodns_resolver",
        return_value=resolves_to_us,
    ):
        await _setup_pointing_at(hass, "http://www.pumpspy.com:8081", free_port)

        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{free_port}/bbs_json", data=b"{}"
            ) as response:
                assert response.status == 502

    assert "192.168.7.57" in caplog.text
