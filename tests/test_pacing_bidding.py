"""Pacing inside the live bidding path (REH-85).

These drive the real `SmartBidding.calculate_ep_bid`, because the whole defect
was that a cap existed in one place and the number that executed came from
another.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.services.bid_ceiling import BidCeilingPolicy, Tier
from rehoboam.services.pacing import PacingContext

CEILING = BidCeilingPolicy(
    floor_eur=250_000,
    tier_pcts={
        Tier.MARGINAL: 8.0,
        Tier.SOLID: 15.0,
        Tier.STRONG: 25.0,
        Tier.MUST_HAVE: 35.0,
    },
)


@pytest.fixture
def bidding():
    return SmartBidding(bid_learner=None, activity_feed_learner=None, ceiling_policy=CEILING)


def _bid(bidding, *, asking, mv, gain, budget, pacing=None):
    return bidding.calculate_ep_bid(
        asking_price=asking,
        market_value=mv,
        expected_points=80.0,
        marginal_ep_gain=gain,
        confidence=0.8,
        current_budget=budget,
        sell_plan=None,
        pacing=pacing,
    ).recommended_bid


def test_without_pacing_the_bid_is_unchanged(bidding):
    """None must be a true no-op, or every existing caller changes behaviour."""
    assert _bid(bidding, asking=10_000_000, mv=10_000_000, gain=90.0, budget=62_307_522) > 0


def test_a_signing_that_would_break_the_reserve_is_not_bid_on(bidding):
    """Tah: EUR 44.1m asking against a EUR 32.4m reserve on a EUR 62.3m budget.

    The cap lands below the asking price, and the existing
    `if recommended_bid < asking_price: recommended_bid = 0` turns that into a
    skip. Pacing therefore needs no refusal path of its own.
    """
    paced = _bid(
        bidding,
        asking=44_068_628,
        mv=37_028_628,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    )
    assert paced == 0


def test_a_signing_that_fits_the_reserve_still_happens(bidding):
    """Asllani: EUR 25.1m leaves EUR 37.2m against a EUR 32.4m reserve."""
    paced = _bid(
        bidding,
        asking=25_058_860,
        mv=23_708_860,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    )
    assert paced >= 25_058_860


def test_pacing_never_raises_a_bid(bidding):
    """It is a cap. It composes with the REH-99 ceiling; it never competes."""
    unpaced = _bid(bidding, asking=5_000_000, mv=5_000_000, gain=90.0, budget=62_307_522)
    paced = _bid(
        bidding,
        asking=5_000_000,
        mv=5_000_000,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    )
    assert paced <= unpaced


def test_open_offers_reduce_what_may_be_committed(bidding):
    """A EUR 30m open offer plus a EUR 32.4m reserve leaves nothing spendable."""
    paced = _bid(
        bidding,
        asking=25_058_860,
        mv=23_708_860,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=30_000_000),
    )
    assert paced == 0


def test_a_trade_pair_is_paced_on_its_net_cost(bidding):
    """The synthetic sell plan is what makes this work with no special case.

    `trader.py` gives a pair a SellPlan whose total_recovery is the sale
    proceeds, so budget_ceiling already includes them. A pair that looks
    unaffordable gross becomes affordable net — which is correct, because a
    pair recycles capital rather than consuming it.
    """
    from rehoboam.scoring.models import SellPlan

    plan = SellPlan(
        players_to_sell=[],
        total_recovery=20_000_000,
        net_budget_after=0,
        is_viable=True,
        ep_impact=0.0,
        reasoning="test",
    )
    gross_only = bidding.calculate_ep_bid(
        asking_price=25_000_000,
        market_value=25_000_000,
        expected_points=80.0,
        marginal_ep_gain=90.0,
        confidence=0.8,
        current_budget=40_000_000,
        sell_plan=None,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    ).recommended_bid
    with_pair = bidding.calculate_ep_bid(
        asking_price=25_000_000,
        market_value=25_000_000,
        expected_points=80.0,
        marginal_ep_gain=90.0,
        confidence=0.8,
        current_budget=40_000_000,
        sell_plan=plan,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    ).recommended_bid
    assert gross_only == 0
    assert with_pair >= 25_000_000
