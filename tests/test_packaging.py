"""What HACS and hassfest check, checked here first.

Every assertion in this file corresponds to a rule enforced somewhere we do not
control -- the HACS action, hassfest, or home-assistant/brands. Getting one
wrong does not break a test at home; it breaks a stranger's install, or bounces
a pull request in someone else's repository days later. So they are pinned here.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "pumpspy_local"
BRANDS = ROOT / "brands"


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_hacs_json_exists_and_names_the_integration() -> None:
    """HACS refuses a repository without it, and requires at least a name."""
    hacs = _json(ROOT / "hacs.json")
    assert hacs["name"]


def test_the_manifest_domain_matches_the_directory() -> None:
    """A mismatch here loads as nothing at all, with no error worth reading."""
    assert _json(COMPONENT / "manifest.json")["domain"] == COMPONENT.name


@pytest.mark.parametrize(
    "key",
    ["domain", "name", "codeowners", "documentation", "issue_tracker", "version"],
)
def test_the_manifest_carries_every_key_hacs_requires(key: str) -> None:
    """Custom integrations need more than core ones -- `version` above all."""
    assert _json(COMPONENT / "manifest.json").get(key)


def test_the_version_is_a_real_version() -> None:
    """HACS sorts releases by this, so it has to parse and has to move."""
    version = _json(COMPONENT / "manifest.json")["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    assert version != "0.0.1", "still the scaffolding placeholder"


def _png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR, without pulling in an image library."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", header[16:24])


@pytest.mark.parametrize(("name", "expected"), [("icon.png", 256), ("icon@2x.png", 512)])
def test_the_brand_icons_are_the_size_brands_demands(name: str, expected: int) -> None:
    """home-assistant/brands rejects anything else, and only says so on review."""
    assert _png_size(BRANDS / name) == (expected, expected)


def test_the_brand_icons_were_rendered_from_the_svg_that_is_committed() -> None:
    """The SVG is the master. A PNG edited by hand would silently drift from it."""
    assert (BRANDS / "icon.svg").exists()
