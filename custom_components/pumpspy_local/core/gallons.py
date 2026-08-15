"""Estimating how much water a pump cycle moved.

Gallons are not reported by the device. The vendor's app derives them from run
duration, and the rule observed in captures is ``floor(time / 10)`` with ``time``
in tenths of a second -- about one gallon per second, the same constant for both
the primary and the backup pump.

This is an estimate by construction, and the entities say so.
"""

from __future__ import annotations

import math

# Gallons per second. Nominal rather than measured, hence configurable: a given
# install's plumbing will not match it exactly.
DEFAULT_FLOW_RATE = 1.0


def estimated_gallons(
    duration_seconds: float, flow_rate: float = DEFAULT_FLOW_RATE
) -> int:
    """Gallons a run of this length is estimated to have moved.

    Rounds down, matching the vendor, so our figure never reads higher than the
    one in their app for the same run.
    """
    return math.floor(duration_seconds * flow_rate)
