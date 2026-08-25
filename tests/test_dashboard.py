"""Every entity the shipped dashboard names must be one the integration creates.

The dashboard is coupled to our entity ids, which is why it lives in this repo at
all. That coupling is invisible: rename a sensor and the dashboard keeps loading,
silently showing "Entity not available" on a flood-prevention display. So the
coupling gets a test.

Entity ids are checked against the *names* in the entity descriptions, not the
keys, because the name is what Home Assistant slugifies into the entity id --
changing a name changes the id while the key stays put.
"""

import re
from pathlib import Path

import pytest
import yaml
from homeassistant.util import slugify

from custom_components.pumpspy_local import binary_sensor, button, event, sensor
from custom_components.pumpspy_local.const import (
    LOCAL_AUTH_ENTITY_NAME,
    SERVICE_DEVICE_NAME,
    VENDOR_ENTITY_NAME,
)

DASHBOARD = (
    Path(__file__).parent.parent / "dashboard" / "pumpspy-dashboard.yaml"
)

# The dashboard ships without a real device id -- ours would be committed
# otherwise, and it would be wrong for everyone else regardless. Lowercase
# because that is what slugify produces, so the placeholder ids are shaped
# exactly like the real ones a find-and-replace will turn them into.
PLACEHOLDER = "your_device_id"

# The one long number allowed to appear in the file. The instructions need a
# concrete example so people recognise the shape of their own id, and a
# whitelist of exactly one obvious fake keeps that possible without softening
# the rule to something a real id could slip through.
EXAMPLE_DEVICE_ID = "123456789012345"

# The integration's own service device owns a couple of entities that describe
# the proxy rather than a pump, so no device id appears in their ids and the
# find-and-replace in the instructions never touches them. They are built by
# hand rather than from an entity description, so the rebuild below cannot see
# them, and the placeholder rule has to let them through explicitly -- naming
# them one by one rather than exempting anything that lacks a device id, which
# would exempt a typo too.
SERVICE_ENTITY_IDS = frozenset(
    f"binary_sensor.{slugify(f'{SERVICE_DEVICE_NAME} {name}')}"
    for name in (VENDOR_ENTITY_NAME, LOCAL_AUTH_ENTITY_NAME)
)


def _entity_ids_the_integration_creates() -> set[str]:
    """Rebuild the entity ids from the descriptions, as HA would."""
    by_domain = {
        "sensor": sensor.SENSORS,
        "binary_sensor": (
            *binary_sensor.BINARY_SENSORS,
            binary_sensor.FIRMWARE_DESCRIPTION,
        ),
        "button": (button.DESCRIPTION, button.APPROVE_DESCRIPTION),
        "event": (event.DESCRIPTION,),
    }
    return {
        f"{domain}.{slugify(f'PumpSpy {PLACEHOLDER} {description.name}')}"
        for domain, descriptions in by_domain.items()
        for description in descriptions
    } | SERVICE_ENTITY_IDS


def _referenced_entity_ids(node: object) -> set[str]:
    """Every entity id the dashboard names, wherever it is nested.

    Lovelace spells entity references several ways -- ``entity:``, an
    ``entities:`` list of bare strings, or that list holding dicts -- so this
    walks the whole tree rather than trusting one shape.
    """
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "entity" and isinstance(value, str):
                found.add(value)
            elif key == "entities" and isinstance(value, list):
                found.update(item for item in value if isinstance(item, str))
            found |= _referenced_entity_ids(value)
    elif isinstance(node, list):
        for item in node:
            found |= _referenced_entity_ids(item)
    return found


@pytest.fixture(name="dashboard")
def dashboard_fixture() -> object:
    assert DASHBOARD.exists(), f"no dashboard at {DASHBOARD}"
    return yaml.safe_load(DASHBOARD.read_text())


def test_the_dashboard_names_entities_at_all(dashboard):
    # Without this the real test below passes happily against a dashboard that
    # references nothing -- including one that failed to parse into anything.
    assert _referenced_entity_ids(dashboard), "no entity references found"


def test_every_entity_the_dashboard_names_is_one_we_create(dashboard):
    ours = _entity_ids_the_integration_creates()

    unknown = sorted(
        entity_id
        for entity_id in _referenced_entity_ids(dashboard)
        if entity_id not in ours
    )

    assert unknown == [], (
        "the dashboard references entities this integration does not create: "
        f"{unknown}"
    )


def test_the_dashboard_can_tell_the_two_silences_apart(dashboard):
    """The stale chips must say *which* failure this is.

    A vendor outage and a broken redirect both end with the display going
    stale, and the vendor sensor is the only thing that separates them. It is
    read from inside a Jinja template as well as from a card condition, and the
    walk above only sees the latter, so this checks the raw text too.
    """
    vendor = f"binary_sensor.{slugify(f'{SERVICE_DEVICE_NAME} {VENDOR_ENTITY_NAME}')}"

    assert vendor in _referenced_entity_ids(dashboard), (
        "no card conditions on vendor reachability"
    )
    assert DASHBOARD.read_text().count(vendor) > 1, (
        "the liveness chip does not consult the vendor sensor, so a stale "
        "dashboard still cannot say which silence it is"
    )


def test_no_real_device_id_appears_anywhere_in_the_dashboard():
    """Not just in entity ids -- anywhere, comments included.

    The entity-id check below only sees values the YAML parser kept, so a real
    device id sitting in an explanatory comment sails straight past it and into
    a public repository. That is exactly how it nearly happened. A device id is
    a long run of digits and nothing else here legitimately is one.
    """
    found = set(re.findall(r"\d{10,}", DASHBOARD.read_text()))

    assert found <= {EXAMPLE_DEVICE_ID}, (
        f"what look like real device ids appear in {DASHBOARD.name}: "
        f"{sorted(found - {EXAMPLE_DEVICE_ID})}. Use {EXAMPLE_DEVICE_ID} in "
        "examples."
    )


def test_the_dashboard_ships_without_a_real_device_id(dashboard):
    # Committing the real one would publish the device's identifier.
    offenders = sorted(
        entity_id
        for entity_id in _referenced_entity_ids(dashboard)
        if PLACEHOLDER not in entity_id and entity_id not in SERVICE_ENTITY_IDS
    )

    assert offenders == [], (
        f"entity ids must use the {PLACEHOLDER} placeholder: {offenders}"
    )
