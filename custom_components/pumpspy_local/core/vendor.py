"""Whether the vendor is answering, judged from how forwarding is going.

When the monitoring chain goes quiet the cause matters, and the two causes
worth telling apart look identical from Home Assistant: the vendor's device API
can stop answering while everything here is healthy, and on 2026-08-20 it did
for over an hour. Local monitoring carried on the whole time -- parsing happens
whether or not the forward succeeded -- and then the device gave up and went
silent, which made a vendor outage look exactly like a dead redirect.

Nothing new has to be measured to tell them apart. Every request forwarded
upstream already succeeds or fails, and that outcome is the signal.

The thresholds are measured from the shim's access log, which records every
request the device made and which upstream served it:

- On healthy traffic -- about 1100 forwarded requests over 22 hours -- the
  longest run of consecutive failures is **two**, once. The vendor hangs up on
  roughly one request in ten even when it is fine.
- Through the outage the runs were **four to twenty-two**, usually seven or
  eight, with isolated single successes scattered between them.

So four consecutive failures is above anything a healthy day produced and below
the outage's runs, and recovery deliberately needs two consecutive successes:
recovering on one would have flapped repeatedly while the vendor was still down.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Both measured -- see the module docstring. They are asymmetric on purpose.
FAILURES_TO_UNREACHABLE = 4
SUCCESSES_TO_REACHABLE = 2


@dataclass
class VendorHealth:
    """A running verdict on the vendor, fed one forward outcome at a time."""

    # None until something has actually been tried. "Never asked" is not the
    # same as "answering fine", and the alert this feeds has to say which.
    reachable: bool | None = None
    last_delivery: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    _consecutive_successes: int = 0

    def record_success(self, now: datetime) -> None:
        """Note that a request reached the vendor and was answered."""
        self.last_delivery = now
        self.consecutive_failures = 0
        self._consecutive_successes += 1
        if self._consecutive_successes >= SUCCESSES_TO_REACHABLE:
            self.reachable = True
        elif self.reachable is None:
            # Nothing to flap against yet, and one answer is proof enough that
            # the vendor is there.
            self.reachable = True

    def record_failure(self, reason: str) -> None:
        """Note that a request could not be delivered, and why."""
        self.last_error = reason
        self._consecutive_successes = 0
        self.consecutive_failures += 1
        if self.consecutive_failures >= FAILURES_TO_UNREACHABLE:
            self.reachable = False
