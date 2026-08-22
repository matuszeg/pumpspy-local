"""The setup guide ships a working automation, so it gets held to that claim.

Two ways a document like this rots without anyone noticing: the YAML stops
parsing, or it keeps parsing while naming an entity the integration no longer
creates. The second is worse, because the automation loads and simply never
fires the branch that mattered.

The entity id is rebuilt the way Home Assistant builds it -- slugified device
name plus slugified entity name -- rather than written out by hand, so renaming
either one fails here instead of in someone's notifications.
"""

import re
from pathlib import Path

import yaml
from homeassistant.util import slugify

from custom_components.pumpspy_local.const import (
    LOCAL_AUTH_ENTITY_NAME,
    SERVICE_DEVICE_NAME,
    VENDOR_ENTITY_NAME,
)

DOCS = Path(__file__).parent.parent / "docs"
SETUP = DOCS / "setup.md"


def _yaml_blocks(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, re.S)


def test_every_yaml_block_in_the_docs_parses():
    for markdown in sorted(DOCS.glob("*.md")):
        for block in _yaml_blocks(markdown.read_text()):
            assert yaml.safe_load(block), f"{markdown.name}: empty or unparsable block"


def test_the_example_automation_names_the_sensor_the_integration_creates():
    expected = (
        f"binary_sensor.{slugify(SERVICE_DEVICE_NAME)}_"
        f"{slugify(VENDOR_ENTITY_NAME)}"
    )
    assert expected in SETUP.read_text()


def test_the_example_automation_reads_the_attributes_that_exist():
    """The message quotes last_delivery; the others are documented alongside it."""
    text = SETUP.read_text()
    for attribute in ("last_delivery", "consecutive_failures", "last_error"):
        assert attribute in text


def test_the_docs_name_the_local_token_sensor_the_integration_creates():
    expected = (
        f"binary_sensor.{slugify(SERVICE_DEVICE_NAME)}_"
        f"{slugify(LOCAL_AUTH_ENTITY_NAME)}"
    )
    assert expected in SETUP.read_text()
