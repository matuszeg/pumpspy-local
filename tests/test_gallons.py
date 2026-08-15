"""Estimated gallons.

Gallons are not on the wire. The vendor's app derives them from run duration,
and the observed rule is floor(time / 10) where time is tenths of a second --
so roughly one gallon per second, using the same constant for both pumps.
"""

from custom_components.pumpspy_local.core.gallons import (
    DEFAULT_FLOW_RATE,
    estimated_gallons,
)


def test_reproduces_the_vendor_figure_at_the_default_rate():
    """The captured run reported time=82, and the app shows 8 gallons."""
    assert estimated_gallons(8.2) == 8


def test_rounds_down_rather_than_to_nearest():
    """Matching the vendor's floor, so our number never reads higher than theirs."""
    assert estimated_gallons(8.9) == 8


def test_the_flow_rate_is_configurable():
    """One gallon per second is nominal, not measured for a given install."""
    assert estimated_gallons(10.0, flow_rate=0.5) == 5


def test_a_run_too_short_to_move_a_gallon_estimates_none():
    assert estimated_gallons(0.4) == 0


def test_the_default_rate_is_one_gallon_per_second():
    assert DEFAULT_FLOW_RATE == 1.0
