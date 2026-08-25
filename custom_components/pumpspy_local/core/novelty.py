"""Say something the first time the device sends us something we do not know.

The device speaks more than we read. Three message types sat in real captures
for months without ever producing a line on a running install: pump alerts were
parsed and then dropped on the floor, unread ping types disappeared inside a
comparison, and paths with no parser went to debug. Any of them could have been
the first sign of a firmware change and none of them would have been noticed.

Two decisions shape this, both from the live install rather than taste:

- **The first sighting warns; everything after it is debug.** Home Assistant
  logs this integration at WARNING there, so an INFO line is invisible in the
  place it matters. But ``/pump_outlet_alerts`` arrives about once a day, and
  something that warns daily is something people filter out. Warning once and
  then going quiet is the only version of this that is both audible and
  bearable.
- **Known-but-unparsed is not news.** Four paths are answered rather than read,
  and warning about them would fire on every healthy install while saying
  nothing at all.
"""

from __future__ import annotations

import logging

from .parser import PARSER_PATHS, BbsReading, ParsedMessage, Ping, PumpAlert
from .state import PING_WIFI_RSSI

_LOGGER = logging.getLogger(__name__)

# Paths that reach us without a parser on purpose, so their silence is expected.
#
# ``/tm`` is the device asking what time it is: the vendor answers
# ``{"utctime": <milliseconds>}``, which is why the device's own timestamps sit
# within a second of ours. ``/oauth/token`` is its login, ``/new_firmware`` is
# the update check, and ``/bbs_parameters`` fetches its configuration block.
# Each of those three is answered elsewhere in this integration.
_ANSWERED_NOT_READ = frozenset(
    {"/tm", "/oauth/token", "/new_firmware", "/bbs_parameters"}
)

# A path we do parse can still arrive here with nothing parsed, because a body
# that failed to parse looks exactly like a path with no parser from outside.
# That case has already been warned about where it happened, and it is not an
# unknown endpoint either way.
_EXPECTED_UNPARSED = _ANSWERED_NOT_READ | PARSER_PATHS


def _root(path: str) -> str:
    """A path with any device id trailing it removed.

    ``/new_firmware/<device id>`` is one endpoint, not one endpoint per device,
    and keeping the id out of the key also keeps it out of the log line. Only
    all-digit segments go: ``/oauth/token`` is two segments and both of them
    are the endpoint.
    """
    segments = [s for s in path.split("?")[0].split("/") if s]
    while segments and segments[-1].isdigit():
        segments.pop()
    return "/" + "/".join(segments)


class Novelties:
    """Remembers what it has already reported, for as long as we are loaded.

    Deliberately not persisted. A restart earning one repeat warning is a fair
    price for not carrying a stale "already told you" across an upgrade that
    might be the very thing that changed the protocol.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, object]] = set()

    def note(self, path: str, parsed: ParsedMessage | None) -> None:
        """Report anything unfamiliar about one request. Never raises."""
        if parsed is None:
            root = _root(path)
            if root not in _EXPECTED_UNPARSED:
                self._say(
                    "path",
                    root,
                    "the device sent %s, which nothing here reads",
                    root,
                )
            return

        if isinstance(parsed, BbsReading):
            for field in parsed.unknown_fields:
                self._say(
                    "bbs field",
                    field,
                    "unknown field %s in a /bbs_json message -- the firmware "
                    "may have changed",
                    field,
                )
            return

        for item in parsed:
            if isinstance(item, PumpAlert):
                self._say(
                    "alert",
                    item.alert_type,
                    "pump alert type %s (value %s) is not read by anything here",
                    item.alert_type,
                    item.value,
                )
            elif isinstance(item, Ping) and item.data_type != PING_WIFI_RSSI:
                self._say(
                    "ping",
                    item.data_type,
                    "ping type %s (value %s) is not read by anything here",
                    item.data_type,
                    item.value,
                )

    def _say(self, kind: str, key: object, message: str, *args: object) -> None:
        if (kind, key) in self._seen:
            _LOGGER.debug(message, *args)
            return
        self._seen.add((kind, key))
        _LOGGER.warning(message, *args)
