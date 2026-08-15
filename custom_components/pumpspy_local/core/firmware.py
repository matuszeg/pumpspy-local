"""Firmware-check policy.

The device asks ``GET /new_firmware/<id>`` roughly every 13 seconds. Because we
sit in that path we can see when the vendor actually offers an update.

What is actually known, from real captures:

- The "no update" reply is ``[]`` -- an empty JSON array, sent chunked, 588
  bytes on the wire once headers and chunk framing are counted. An earlier note
  described a "105-148 byte response"; those were *request* Content-Lengths,
  misread. Detection keys off the empty array, never a size threshold: a
  threshold built on those numbers would have called every poll an update.
- No real update has ever been captured. The largest response payload of any
  kind is 168 bytes.

So the only trustworthy signal is "this is not the no-update reply", and the
rules below are deliberately conservative about the rest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

# The device polls this endpoint every ~13 seconds. Relaying all of that upstream
# is pointless traffic, so a reply is cached and refreshed at most this often.
DEFAULT_CHECK_INTERVAL = timedelta(hours=24)


class Verdict(Enum):
    """What the vendor's reply appears to be."""

    NO_UPDATE = "no_update"
    UPDATE_OFFERED = "update_offered"
    # Neither -- an upstream error, or a shape we have never seen. Deliberately
    # distinct from UPDATE_OFFERED so a bad day at the vendor cannot raise a
    # firmware alert or, in quarantine mode, start withholding replies.
    UNKNOWN = "unknown"


def classify(status: int, body: bytes) -> Verdict:
    """Judge one upstream firmware-check reply."""
    if 300 <= status < 400:
        # Most likely pointing at the image itself.
        return Verdict.UPDATE_OFFERED

    if status != 200:
        return Verdict.UNKNOWN

    if not body.strip():
        return Verdict.UNKNOWN

    try:
        payload = json.loads(body)
    except ValueError:
        # Not JSON at all. Could be an image, could be an error page. Treat it
        # as an update: the default mode only alerts, and missing a real update
        # defeats the point of watching this endpoint.
        return Verdict.UPDATE_OFFERED

    return Verdict.NO_UPDATE if payload == [] else Verdict.UPDATE_OFFERED


@dataclass(frozen=True)
class Reply:
    """An upstream firmware-check reply."""

    status: int
    body: bytes


@dataclass
class FirmwareChecker:
    """Throttles the check, and optionally holds an update back.

    Precedence, in order: a held update beats the cache, and the cache beats
    asking upstream. That ordering is what stops a scheduled refresh from
    eventually handing the device the very update the user asked to hold.
    """

    interval: timedelta = DEFAULT_CHECK_INTERVAL
    cached: Reply | None = None
    last_checked: datetime | None = None
    held: Reply | None = None
    # Whether the vendor is currently offering an update, in either mode.
    update_offered: bool = False

    def should_query_upstream(self, now: datetime) -> bool:
        if self.held is not None:
            return False
        if self.cached is None or self.last_checked is None:
            # Nothing to answer the device with yet, so we have to ask.
            return True
        return now - self.last_checked >= self.interval

    def reply_for_device(self) -> Reply | None:
        """What to send without asking upstream, if anything."""
        return self.cached

    def record_upstream(
        self, now: datetime, reply: Reply, quarantine: bool
    ) -> Reply:
        """Fold in an upstream reply and return what the device should get."""
        verdict = classify(reply.status, reply.body)

        if verdict is Verdict.NO_UPDATE:
            self.update_offered = False
            self.cached = reply
            self.last_checked = now
            return reply

        if verdict is Verdict.UPDATE_OFFERED:
            self.update_offered = True
            self.last_checked = now
            # Quarantine needs a known-good reply to answer with. Without one
            # there is nothing safe to send, and inventing a shape the device
            # has never seen is worse than letting the update through.
            if quarantine and self.cached is not None:
                self.held = reply
                return self.cached
            return reply

        # UNKNOWN: an upstream error or a shape we do not recognise. Do not
        # cache it, do not let it look like an update, and leave any standing
        # offer alone -- a bad gateway is not evidence the update went away.
        self.last_checked = now
        return reply

    def approve(self) -> None:
        """Release a held update so the next check can deliver it."""
        self.held = None
        # Force a fresh query rather than replaying the stored copy: by now the
        # vendor may be offering something different again.
        self.last_checked = None
