"""REH-69: bid sizing must still discriminate on the real-points scale.

`max_bid_fraction = min(0.8, 0.2 + marginal_ep_gain / 50)` was calibrated for
the 0-100 index, where it ramped 0.30 -> 0.70 across the gains the bot saw.
On real points every gain that clears the shipped floor of 40 saturates at
0.80, so a +43 signing and a +195 superstar are sized identically. The bot
commits 80% of budget to the first qualifying candidate and cannot afford the
one that matters next week.

REH-55 migrated every NAMED threshold and missed this one because it is
arithmetic buried mid-function, and because no test asserted the gradient's
SHAPE — only its endpoints. That missing test class is the point of this file.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import (
    BID_FRACTION_MAX,
    BID_FRACTION_MIN,
    max_bid_fraction,
)


def test_the_fraction_rises_across_the_real_operating_range():
    """The regression proper. Every one of these clears the shipped floor of
    40, so under the old formula they were all identical at 0.80."""
    solid = max_bid_fraction(43.0)
    strong = max_bid_fraction(53.0)
    must_have = max_bid_fraction(70.0)
    exceptional = max_bid_fraction(82.0)

    assert solid < strong < must_have <= exceptional


def test_a_superstar_is_sized_above_a_merely_qualifying_candidate():
    """The behaviour the saturation destroyed: p50 and the maximum observed
    gain (176.2) must not be treated as the same commitment."""
    assert max_bid_fraction(176.2) > max_bid_fraction(43.0)


def test_the_fraction_is_clamped_at_both_ends():
    assert max_bid_fraction(1000.0) == BID_FRACTION_MAX
    assert max_bid_fraction(0.0) == BID_FRACTION_MIN
    assert max_bid_fraction(-50.0) == BID_FRACTION_MIN


def test_full_commitment_needs_an_exceptional_gain():
    """0.80 of budget is the whole war chest. It should require a gain near the
    top of the measured distribution (p95 = 82.0), not merely clearing p50."""
    assert max_bid_fraction(82.0) == pytest.approx(BID_FRACTION_MAX)
    assert max_bid_fraction(43.0) < 0.4


def test_the_gradient_is_tunable_without_a_deploy():
    """Like the tiers (REH-55): the first real evidence arrives mid-season."""
    from rehoboam.config import Settings

    field = Settings.model_fields["bid_full_commit_gain"]
    assert field.default == pytest.approx(82.0)
    assert "real points" in (field.description or "").lower()
