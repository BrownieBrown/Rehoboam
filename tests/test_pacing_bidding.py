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
    """`pacing=None` must be a true no-op, or every existing caller changes
    behaviour — `trader.py` and `replay/driver.py` never pass the argument at
    all, which defaults to `None`.

    Asserting `> 0` (the old assertion) only proves *some* bid came back; it
    would still pass even if the default silently changed what that bid was.
    Assert the two calls — explicit `pacing=None` and the argument omitted
    entirely — return the exact same integer.
    """
    kwargs = {"asking": 10_000_000, "mv": 10_000_000, "gain": 90.0, "budget": 62_307_522}
    explicit_none = _bid(bidding, **kwargs, pacing=None)
    omitted = bidding.calculate_ep_bid(
        asking_price=kwargs["asking"],
        market_value=kwargs["mv"],
        expected_points=80.0,
        marginal_ep_gain=kwargs["gain"],
        confidence=0.8,
        current_budget=kwargs["budget"],
        sell_plan=None,
        # pacing intentionally omitted — this is what every real caller does today.
    ).recommended_bid
    assert explicit_none == omitted
    assert explicit_none > 0


def test_the_cap_must_sit_after_the_market_value_floor(bidding):
    """Pins placement: pacing must be applied AFTER the market-value floor
    (`recommended_bid = min(market_value_floor, budget_ceiling)`), or that
    floor silently undoes the cap.

    Numbers are chosen so the two placements diverge, not just so the
    end-state assertion holds:

    - asking=EUR 30.0m sits just above pace_cap=EUR 29.907m (budget EUR
      62.307522m - reserve EUR 32.4m - open_offers 0), so a *correctly*
      placed cap lands below asking and the existing
      `if recommended_bid < asking_price: recommended_bid = 0` zeroes it.
    - market_value * 1.01 = EUR 30.098m is ABOVE asking_price. If the cap
      were applied before the market-value floor instead, the floor would
      raise the capped bid straight back up to EUR 30.098m — above asking,
      so it would NOT get zeroed, and this test would fail.

    The previous version of this test (Tah, EUR 44.1m asking / EUR 37.0m mv)
    passed regardless of placement: `mv * 1.01` there is *below* asking, so
    the floor could never rescue a misplaced cap above the asking price, and
    the assertion was `== 0` either way. Verified directly (see task-5
    fix report): temporarily moving the pacing block to before the
    market-value floor made this test FAIL and left the old Tah-shaped test
    passing.
    """
    paced = _bid(
        bidding,
        asking=30_000_000,
        mv=29_800_000,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    )
    assert paced == 0


def test_a_signing_that_would_break_the_reserve_is_not_bid_on(bidding):
    """Tah: EUR 44.1m asking against a EUR 32.4m reserve on a EUR 62.3m budget.

    The cap lands below the asking price, and the existing
    `if recommended_bid < asking_price: recommended_bid = 0` turns that into a
    skip. Pacing therefore needs no refusal path of its own.

    Kept as a realistic scenario alongside
    `test_the_cap_must_sit_after_the_market_value_floor` above, which is the
    one that actually discriminates cap placement — here `market_value * 1.01`
    (~EUR 37.4m) stays below the EUR 44.1m asking price, so this test passes
    at `paced == 0` whether the cap is placed correctly or not.
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


def test_a_net_positive_pair_is_not_frozen_by_an_unaffordable_reserve(bidding):
    """Finding 1: at 15/15 the reserve must not freeze a trade that RECOVERS
    money. Squad 15/15 (in_season_min_moves=2, median 10.8m -> reserve
    21.6m), budget already down to EUR 2.0m, selling a 12.0m player to buy an
    8.86m one -- a trade that nets +3.14m into the budget.

    Before the clamp: budget_ceiling = 2.0m + 12.0m recovery = 14.0m, and the
    21.6m reserve alone exceeds that, so pace_cap = 0 and the bid is refused
    even though the squad ends up with MORE money than it has today. The
    clamp caps the reserve at what is actually spendable right now
    (current_budget - open_offers = 2.0m), so the cap becomes 12.0m and the
    trade proceeds.
    """
    from rehoboam.scoring.models import SellPlan

    plan = SellPlan(
        players_to_sell=[],
        total_recovery=12_000_000,
        net_budget_after=2_000_000 + 12_000_000 - 8_860_000,
        is_viable=True,
        ep_impact=0.0,
        reasoning="test",
    )
    paced = bidding.calculate_ep_bid(
        asking_price=8_860_000,
        market_value=8_860_000,
        expected_points=80.0,
        marginal_ep_gain=90.0,
        confidence=0.8,
        current_budget=2_000_000,
        sell_plan=plan,
        pacing=PacingContext(reserve=21_600_000, open_offers=0),
    ).recommended_bid
    assert paced >= 8_860_000
