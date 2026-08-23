"""The shipped nginx shim config, guarded where it fails silently.

None of this can be caught by running nginx and looking at it: every mistake
here still serves traffic. Buffer the device's bodyless GETs and the firmware
polls die while telemetry keeps flowing. Let nginx mark Home Assistant down on a
single failure and telemetry quietly routes around it while both ends look
healthy. Ship a real address instead of the placeholder and someone else's
install points at a machine on our LAN.

So the two load-bearing directives get a test that says why, and the list of
buffered POST paths is tied to the parser's, since a path added there without
being added here loses the replay-to-the-vendor behaviour with nothing to show
for it.
"""

import re
from pathlib import Path

import pytest

from custom_components.pumpspy_local.core.parser import _PARSERS

SHIM = Path(__file__).parent.parent / "shim" / "pumpspy-shim.conf"
OVERRIDE = Path(__file__).parent.parent / "shim" / "nginx-service-override.conf"


@pytest.fixture(name="config")
def config_fixture() -> str:
    return SHIM.read_text()


@pytest.fixture(name="override")
def override_fixture() -> str:
    return OVERRIDE.read_text()


def _block(config: str, header: str) -> str:
    """Return the body of the location block introduced by ``header``."""
    start = config.index(header) + len(header)
    depth = 1
    for offset, char in enumerate(config[start:]):
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth == 0:
            return config[start : start + offset]
    raise AssertionError(f"unterminated block: {header}")


def test_the_devices_bodyless_gets_are_not_buffered(config: str) -> None:
    """Every GET declares a body it never sends; waiting for it wedges nginx."""
    assert "proxy_request_buffering off;" in _block(config, "location / {")


def test_home_assistant_is_judged_one_request_at_a_time(config: str) -> None:
    """Marking it down would divert telemetry away from the thing recording it."""
    assert re.search(r"server\s+HOME_ASSISTANT:8081\s+max_fails=0;", config)


def test_the_vendor_is_the_fallback_and_never_the_default(config: str) -> None:
    assert re.search(r"server\s+www\.pumpspy\.com:8081\s+backup;", config)


def test_every_post_the_parser_knows_can_be_replayed_to_the_vendor(
    config: str,
) -> None:
    """Buffered paths must track the parser's, or a new one loses the replay."""
    listed = re.search(r"location ~ \^/\(([^)]+)\)\$", config)
    assert listed, "the buffered POST location is missing"
    assert {f"/{path}" for path in listed.group(1).split("|")} == set(_PARSERS)


def test_it_ships_a_placeholder_rather_than_an_address(config: str) -> None:
    """Nobody else's Home Assistant lives where ours does."""
    assert "HOME_ASSISTANT:8081" in config
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", config)


def test_no_device_id_can_hide_in_it(config: str) -> None:
    """The same rule the dashboard is held to: no id-shaped number, anywhere."""
    assert not re.search(r"\d{10,}", config)


def test_nginx_is_ordered_after_name_resolution(override: str) -> None:
    """It resolves the vendor while parsing, so starting before DNS is fatal."""
    assert re.search(r"^After=.*\bnss-lookup\.target\b", override, re.MULTILINE)


def test_a_start_that_beat_dns_is_retried(override: str) -> None:
    """Ordering is not a guarantee. Without this, nginx exits once and stays down,
    and the withdrawn redirect hides it: the vendor's app looks fine throughout."""
    assert re.search(r"^Restart=on-failure$", override, re.MULTILINE)
    assert re.search(r"^RestartSec=", override, re.MULTILINE)


def test_the_retrying_is_never_given_up_on(override: str) -> None:
    """systemd stops retrying by default. A shim that gives up is a shim that is
    down for good after one unlucky boot."""
    assert re.search(r"^StartLimitIntervalSec=0$", override, re.MULTILINE)
