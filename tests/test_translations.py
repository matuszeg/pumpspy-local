"""The setup dialog has to be readable by a human.

``strings.json`` is the source file Home Assistant's own build tooling turns
into ``translations/en.json``. A custom integration gets no such build step, so
shipping only ``strings.json`` means the dialog renders raw config keys --
"firmware_policy", "upstream_ip" -- with no help text and no option labels.

Nothing else catches this: the flow works, the schema serialises, every test
passes, and the dialog is still unreadable. Found the first time the real
dialog was opened on a real instance.
"""

import json
from pathlib import Path

from custom_components.pumpspy_local.config_flow import SCHEMA

COMPONENT = (
    Path(__file__).parent.parent / "custom_components" / "pumpspy_local"
)
STRINGS = COMPONENT / "strings.json"
ENGLISH = COMPONENT / "translations" / "en.json"


def test_english_translations_are_shipped():
    assert ENGLISH.is_file(), (
        f"{ENGLISH.relative_to(COMPONENT.parent.parent)} is missing. Home "
        "Assistant reads translations from translations/<lang>.json at "
        "runtime; strings.json alone leaves the dialog showing raw keys."
    )


def test_the_translations_match_the_source_strings():
    # Two copies of the same text drift silently. This is the only thing
    # keeping them honest.
    assert json.loads(ENGLISH.read_text()) == json.loads(STRINGS.read_text())


def test_every_field_in_the_dialog_has_a_label():
    labels = json.loads(ENGLISH.read_text())["config"]["step"]["user"]["data"]

    missing = sorted(str(key) for key in SCHEMA.schema if str(key) not in labels)

    assert missing == [], f"fields would render as raw keys: {missing}"


def test_every_field_in_the_dialog_is_explained():
    """The help text under each field is where the reasoning lives.

    These settings are not self-evident -- why a separate nameserver exists at
    all only makes sense with the explanation attached.
    """
    described = json.loads(ENGLISH.read_text())["config"]["step"]["user"][
        "data_description"
    ]

    missing = sorted(str(key) for key in SCHEMA.schema if str(key) not in described)

    assert missing == [], f"fields with no explanation: {missing}"
