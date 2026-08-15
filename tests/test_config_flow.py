"""The setup dialog.

A config entry is not cosmetic here: Home Assistant only creates device-registry
entries for entities that belong to one, so without it the sensors cannot be
grouped under a device at all.
"""

import pytest
import voluptuous_serialize
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv

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
