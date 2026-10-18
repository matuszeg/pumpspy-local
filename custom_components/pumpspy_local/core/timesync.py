"""Answering the device's clock sync when the vendor cannot.

``GET /tm`` is the device asking the vendor what time it is, on every reconnect
and on its four-hourly re-authentication beat (#13). A minted token (see
``auth.py``) is not enough to get it reporting again through an outage:
measured 2026-10-06 to 10-18, every evening outage ran /oauth/token (minted) ->
/tm (502) -> /oauth/token again two minutes later, 83 mints in 13 days, and
the loop only ended when the vendor answered /tm. So the clock sync is the step
the device was still waiting on.

Gated exactly like minting, and for the same reason: a vendor that is up gets
the last word. Answering is also only ever second best here, since Home
Assistant's clock is good but the vendor's is the one the device is built to
trust.
"""

from __future__ import annotations

from datetime import datetime

# What the vendor sends, from a capture taken 2026-08-13: compact JSON, one
# key, UTC milliseconds since the epoch.
TIME_CONTENT_TYPE = "application/json;charset=UTF-8"


def should_answer_time(
    vendor_reachable: bool | None, relayed_status: int | None
) -> bool:
    """Whether to answer this clock sync ourselves.

    ``relayed_status`` is what the vendor said, or None if the request could
    not be delivered at all.
    """
    # None means nothing has been forwarded yet, which is not an outage.
    if vendor_reachable is not False:
        return False
    # A vendor that does answer the clock, mid-outage or not, is believed.
    return relayed_status != 200


def time_response(now: datetime) -> bytes:
    """The body the vendor would have sent, for an aware ``now``."""
    return b'{"utctime":%d}' % round(now.timestamp() * 1000)
