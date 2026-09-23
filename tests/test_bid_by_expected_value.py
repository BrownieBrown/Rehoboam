"""The bid maximises expected value, with the runner-up priced in.

With a price per expected point (the league's own, from the fair-price fit)
the bidder no longer reads the win curve at a typed quantile: it chooses the
premium where win share x (gain over the next-best candidate, in euros, minus
the premium) is largest. An interchangeable player is worth almost nothing to
win; a unique one is worth paying up for; an empty slot near kickoff adds its
-100 points. Without a price per point the quantile read is the fallback.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.config import Settings
from rehoboam.services.ceiling_derivation import WinnerRow
from rehoboam.services.win_curve import WinCurve

POLICY = Settings(kickbase_email="test@example.com", kickbase_password="x").bid_ceiling_policy()
BANDS = (0, 5_000_000, 15_000_000)
B = 255_000.0


def _curve():
    rows = [WinnerRow(20_000_000, 4.0 + i * (11 / 39)) for i in range(40)]  # 4..15, p75 12.25
    return WinCurve.from_rows(rows, bands=BANDS, min_sample=30)


def _bidder(**kw):
    return SmartBidding(ceiling_policy=POLICY, win_curve=_curve(), eur_per_point=B, **kw)


def _bid(bidder, *, gain=75.0, alternative=None, urgent=False):
    return bidder.calculate_ep_bid(
        asking_price=18_000_000,
        market_value=18_000_000,
        expected_points=gain,
        marginal_ep_gain=gain,
        confidence=0.9,
        current_budget=500_000_000,
        player_id="p",
        trend_change_pct=0.0,
        offer_count=0,
        alternative_gain=alternative,
        urgent=urgent,
    )


class TestTheValueBid:
    def test_a_unique_large_gain_pays_up(self):
        rec = _bid(_bidder(), gain=75.0, alternative=0.0)
        assert rec.overbid_pct >= 12.0
        assert "value bid: unique gain +75.0 pts" in rec.reasoning

    def test_an_interchangeable_player_is_worth_little_to_win(self):
        unique = _bid(_bidder(), gain=46.3, alternative=0.0)
        contested = _bid(_bidder(), gain=46.3, alternative=41.0)  # Burger vs Schick
        assert contested.overbid_pct < unique.overbid_pct
        assert contested.overbid_pct <= 7.0
        assert "next best +41.0" in contested.reasoning

    def test_an_alternative_at_least_as_good_falls_back_to_the_quantile_read(self):
        rec = _bid(_bidder(), gain=40.0, alternative=45.0)
        assert "value bid" not in rec.reasoning
        assert "curve p" in rec.reasoning

    def test_an_empty_slot_near_kickoff_adds_its_penalty(self):
        calm = _bid(_bidder(), gain=5.0, alternative=0.0)
        urgent = _bid(_bidder(), gain=5.0, alternative=0.0, urgent=True)
        assert "worth EUR 1,275,000" in calm.reasoning
        assert "empty slot +100" in urgent.reasoning
        assert "worth EUR 26,775,000" in urgent.reasoning
        assert urgent.overbid_pct >= calm.overbid_pct

    def test_the_tier_no_longer_sets_the_premium(self):
        # Same unique gain, different tiers (via the strong/must-have bands): same premium.
        strong = _bid(_bidder(), gain=50.0, alternative=0.0)
        must = _bid(_bidder(), gain=70.0, alternative=20.0)  # same 50-point unique gain
        assert strong.overbid_pct == pytest.approx(must.overbid_pct, abs=0.6)


class TestFallbacks:
    def test_without_a_price_per_point_the_quantile_read_stands(self):
        bidder = SmartBidding(ceiling_policy=POLICY, win_curve=_curve())
        rec = _bid(bidder, gain=75.0, alternative=0.0)
        assert rec.overbid_pct == pytest.approx(12.25, abs=0.2)
        assert "curve p75" in rec.reasoning

    def test_the_ceiling_still_caps_the_value_bid(self):
        rec = _bid(_bidder(), gain=300.0, alternative=0.0)
        assert rec.recommended_bid <= POLICY.max_bid(18_000_000, "must_have")

    def test_a_falling_trend_still_cuts_it(self):
        bidder = _bidder()
        flat = _bid(bidder, gain=75.0, alternative=0.0)
        falling = bidder.calculate_ep_bid(
            asking_price=18_000_000,
            market_value=18_000_000,
            expected_points=75.0,
            marginal_ep_gain=75.0,
            confidence=0.9,
            current_budget=500_000_000,
            player_id="p",
            trend_change_pct=-19.4,
            offer_count=0,
            alternative_gain=0.0,
        )
        assert falling.overbid_pct == pytest.approx(flat.overbid_pct * 0.3, abs=0.3)
