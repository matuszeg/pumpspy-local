"""Answering the device's token request when the vendor cannot.

Everything downstream of the device is independent of the vendor's cloud; the
device is not. When the vendor's API stops answering, the device tolerates the
failures for a while, then decides its token has gone stale and stops sending
telemetry until something issues it a new one. Measured on 2026-08-21: the
outage began at 22:14 and the first /oauth/token went out at 22:23:05, nine
minutes later. Through the longer outage on 2026-08-20 the device sent no
/bbs_json at all for 87 minutes, while /pings carried on the whole time -- so
what stops is precisely the telemetry the entities are built from.

One good answer ends it. At 22:27:26 on 2026-08-21 a single /oauth/token 200
was followed within twelve seconds by /tm, /pings, /bbs_parameters and
/bbs_json. Nothing else needs faking, and nothing is sent to the vendor: this
is our own device, on our own network, asking us a question.

The policy below is deliberately narrow, because the failure that matters is
not the vendor being down. The account the device authenticates as belongs to
whoever installed it, so it could one day be revoked -- and a 401 from a vendor
that is answering is an answer. Minting on that would leave the device happily
reporting here while its vendor delivery was permanently dead, with nothing
saying so. So a token is minted only when the vendor is already judged
unreachable by the measured signal in ``vendor.py``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

# What the vendor answers with, from a capture taken 2026-08-14. Four keys in
# this order, both tokens lowercase UUIDs, and no expires_in -- the device
# re-authenticates on its own four-hourly clock rather than on expiry, so
# nothing here has to carry a lifetime.
AUTH_CONTENT_TYPE = "application/json;charset=UTF-8"

TOKEN_TYPE = "bearer"
SCOPE = "read"


def _new_token() -> str:
    return str(uuid.uuid4())


def should_mint(vendor_reachable: bool | None, relayed_status: int | None) -> bool:
    """Whether to answer this token request ourselves.

    ``relayed_status`` is what the vendor said, or None if the request could
    not be delivered at all.
    """
    # None means nothing has been forwarded yet, which is not the same as an
    # outage. Only the settled negative verdict counts.
    if vendor_reachable is not False:
        return False
    # If the vendor answered the token request, its answer is the right one to
    # pass on, whatever else is failing.
    return relayed_status != 200


@dataclass
class LocalAuth:
    """Tokens issued here, and whether the device is carrying one."""

    issued_at: datetime | None = None

    @property
    def issued(self) -> bool:
        """Whether the device is holding a token the vendor never issued."""
        return self.issued_at is not None

    def mint(
        self, now: datetime, token_factory: Callable[[], str] = _new_token
    ) -> bytes:
        """A token response in the shape the device was seen to accept."""
        self.issued_at = now
        return json.dumps(
            {
                "access_token": token_factory(),
                "token_type": TOKEN_TYPE,
                "refresh_token": token_factory(),
                "scope": SCOPE,
            },
            separators=(",", ":"),
        ).encode()

    def clear(self) -> None:
        """Note that the device is back on a token the vendor issued."""
        self.issued_at = None
