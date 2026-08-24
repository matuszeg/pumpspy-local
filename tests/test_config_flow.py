"""The setup dialog.

A config entry is not cosmetic here: Home Assistant only creates device-registry
entries for entities that belong to one, so without it the sensors cannot be
grouped under a device at all.
"""

import socket

import pytest
import voluptuous_serialize
from aiohttp import ClientSession
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pumpspy_local.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
async def unload_entries_afterwards(hass):
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)


async def test_the_form_is_offered(hass, socket_enabled):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_the_form_can_be_serialised_for_the_frontend(hass, socket_enabled):
    """Driving the flow from Python is not enough to know the dialog works.

    The UI fetches the form over HTTP, which converts the schema to JSON. A
    validator that cannot be converted returns a 500 and the dialog never
    opens, while a Python-level test of the same flow passes happily.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    voluptuous_serialize.convert(
        result["data_schema"], custom_serializer=cv.custom_serializer
    )


async def test_completing_the_form_creates_an_entry(hass, upstream, free_port):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"port": free_port, "upstream": f"http://{upstream.host}:{upstream.port}"},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["port"] == free_port


async def test_only_one_instance_can_be_configured(hass, upstream, free_port):
    """One listener, one port. A second entry would just fail to bind."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        first["flow_id"],
        {"port": free_port, "upstream": f"http://{upstream.host}:{upstream.port}"},
    )
    await hass.async_block_till_done()

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert second["type"] == FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"


async def test_the_form_offers_a_way_to_locate_the_vendor(hass, socket_enabled):
    """The redirect breaks ordinary name lookup for this host as well.

    Without a resolver of its own, Home Assistant asks the poisoned resolver
    where the vendor is and is told "you are". These two fields are the way out
    of that, so the dialog has to actually offer them.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    fields = {str(key) for key in result["data_schema"].schema}

    assert {"nameserver", "upstream_ip"} <= fields


async def test_the_vendor_can_be_pinned_to_an_address(hass, upstream, free_port):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "port": free_port,
            "upstream": f"http://{upstream.host}:{upstream.port}",
            "upstream_ip": "206.80.104.221",
        },
    )
    await hass.async_block_till_done()

    assert result["data"]["upstream_ip"] == "206.80.104.221"


def _values_shown(schema) -> dict:
    """What the dialog would arrive pre-filled with.

    Home Assistant carries a pre-filled value as a suggestion rather than a
    default, so read the suggestion first and fall back to the default.
    """
    shown = {}
    for key in schema.schema:
        suggested = (key.description or {}).get("suggested_value")
        shown[str(key)] = suggested if suggested is not None else key.default()
    return shown


async def _configured(hass, upstream, port) -> MockConfigEntry:
    """An entry that is set up and listening, as a real one would be."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"port": port, "upstream": f"http://{upstream.host}:{upstream.port}"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_the_reconfigure_form_offers_the_current_settings(
    hass, upstream, free_port
):
    """Editing settings has to start from what is configured now.

    An empty form would mean retyping every field to change one of them, which
    is the tear-it-out-and-start-over problem in a nicer costume.
    """
    entry = await _configured(hass, upstream, free_port)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    shown = _values_shown(result["data_schema"])
    assert shown["port"] == free_port
    assert shown["upstream"] == f"http://{upstream.host}:{upstream.port}"


async def test_a_changed_setting_is_saved_and_takes_effect_at_once(
    hass, upstream, free_port
):
    """Writing the entry is not enough: the running listener has to be rebuilt.

    Settings are read once, in async_setup_entry. Without a reload the dialog
    would report success while the integration carried on with the old values.
    """
    entry = await _configured(hass, upstream, free_port)
    result = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "port": free_port,
            "upstream": f"http://{upstream.host}:{upstream.port}",
            "flow_rate": 2.5,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["flow_rate"] == 2.5
    assert hass.data[DOMAIN][entry.entry_id].flow_rate == 2.5


async def test_a_port_that_is_already_taken_is_refused_before_anything_changes(
    hass, upstream, free_port
):
    """A typo in the port must not cost the user a working integration.

    Saving first and finding out at reload leaves the entry dead and the
    listener gone, which is a worse version of the problem this step exists to
    solve. So check the port while the old one is still serving.
    """
    entry = await _configured(hass, upstream, free_port)

    with socket.socket() as taken:
        # Every interface, not just loopback. macOS lets a wildcard bind sit
        # alongside a loopback one, so a loopback-only squatter is not a clash
        # here at all -- and the listener binds the wildcard.
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "port": taken.getsockname()[1],
                "upstream": f"http://{upstream.host}:{upstream.port}",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"port": "port_in_use"}
    assert entry.data["port"] == free_port

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{free_port}/bbs_json", data=b'{"deviceid":"X"}'
        ) as response:
            assert response.status == 200


async def test_keeping_the_current_port_is_not_mistaken_for_a_clash(
    hass, upstream, free_port
):
    """Our own listener holds the configured port, so it always looks taken.

    Probing it unconditionally would make every other setting uneditable, and
    the check would look like it was working the whole time.
    """
    entry = await _configured(hass, upstream, free_port)
    result = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "port": free_port,
            "upstream": f"http://{upstream.host}:{upstream.port}",
            "flow_rate": 1.5,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert entry.data["flow_rate"] == 1.5


async def test_the_reconfigure_form_can_be_serialised_for_the_frontend(
    hass, upstream, free_port
):
    """Same trap as the setup form, one dialog further along.

    A schema this step cannot serialise returns a 500 and the dialog never
    opens, while every Python-level test of the same flow passes.
    """
    entry = await _configured(hass, upstream, free_port)

    result = await entry.start_reconfigure_flow(hass)

    voluptuous_serialize.convert(
        result["data_schema"], custom_serializer=cv.custom_serializer
    )
