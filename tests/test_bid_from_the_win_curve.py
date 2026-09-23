"""The bid is read off what the league's winners paid, not stacked from constants.

`SmartBidding` used to build its overbid from typed percentages (5% base, tier
bonus, confidence, league activity, demand) or an EP-derived "learned"
number that had never seen a rival's bid. With a `WinCurve` from
`transfer_premiums` the base is the premium at the tier's quantile of the
band's winning bids. Everything downstream is unchanged: the trend factor,
the contested bump, the ceiling and the euro floor still apply, and without
a curve the old stack is the fallback.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.config import Settings
from rehoboam.services.ceiling_derivation import WinnerRow
from rehoboam.services.win_curve import WinCurve

SETTINGS = Settings(kickbase_email="test@example.com", kickbase_password="x")
POLICY = SETTINGS.bid_ceiling_policy()
BANDS = (0, 5_000_000, 15_000_000)


def _curve(n=40):
    rows = [WinnerRow(20_000_000, 4.0 + i * (11 / (n - 1))) for i in range(n)]  # 4..15
    rows += [WinnerRow(8_000_000, 2.0 + i * (30 / (n - 1))) for i in range(n)]  # 2..32
    rows += [WinnerRow(2_000_000, i * (100 / (n - 1))) for i in range(n)]  # 0..100
    return WinCurve.from_rows(rows, bands=BANDS, min_sample=30)


class _LearnerAskingFor:
    def __init__(self, pct):
        self.pct = pct

    def get_ep_recommended_overbid(self, **_kw):
        return {"recommended_overbid_pct": self.pct, "reason": "test"}


def _bid(bidder, *, market_value=20_000_000, gain=75.0, trend=0.0, offers=0):
    return bidder.calculate_ep_bid(
        asking_price=market_value,
        market_value=market_value,
        expected_points=gain,
        marginal_ep_gain=gain,
        confidence=0.9,
        current_budget=500_000_000,
        player_id="p",
        trend_change_pct=trend,
        offer_count=offers,
    )


class TestTheCurveIsTheBase:
    def test_a_must_have_bids_the_bands_p75(self):
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()))
        assert rec.overbid_pct == pytest.approx(4.0 + 0.75 * 11, abs=0.2)
        assert "curve p75 of 40 band buys" in rec.reasoning

    def test_a_strong_upgrade_bids_the_median_winner(self):
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()), gain=50.0)
        assert rec.overbid_pct == pytest.approx(9.5, abs=0.2)

    def test_a_solid_upgrade_bids_the_first_quartile(self):
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()), gain=30.0)
        assert rec.overbid_pct == pytest.approx(4.0 + 0.25 * 11, abs=0.2)

    def test_a_marginal_candidate_bids_the_floor(self):
        # Quantile 0 of a band whose cheapest winner paid +4%: 4%, but the
        # marginal ceiling (8%) and the euro floor still frame it.
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()), gain=5.0)
        assert 0 < rec.overbid_pct <= 8.0

    def test_the_learned_override_is_ignored_when_there_is_a_curve(self):
        bidder = SmartBidding(
            ceiling_policy=POLICY, win_curve=_curve(), bid_learner=_LearnerAskingFor(40.0)
        )
        assert _bid(bidder).overbid_pct == pytest.approx(4.0 + 0.75 * 11, abs=0.2)

    def test_the_quantiles_are_configurable(self):
        bidder = SmartBidding(
            ceiling_policy=POLICY, win_curve=_curve(), curve_quantiles={"must_have": 0.5}
        )
        assert _bid(bidder).overbid_pct == pytest.approx(9.5, abs=0.2)


class TestEverythingDownstreamStillApplies:
    def test_a_falling_trend_still_cuts_the_curve_bid(self):
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()), trend=-19.4)
        assert rec.overbid_pct == pytest.approx((4.0 + 0.75 * 11) * 0.3, abs=0.2)

    def test_a_contested_listing_still_gets_the_bump(self):
        bidder = SmartBidding(ceiling_policy=POLICY, win_curve=_curve())
        assert _bid(bidder, offers=4).overbid_pct - _bid(bidder).overbid_pct == pytest.approx(
            6.0, abs=0.2
        )

    def test_the_ceiling_still_caps_a_curve_above_it(self):
        # Under 5m the p75 is 75%; the must-have ceiling is 35% (or the euro floor).
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()), market_value=2_000_000)
        assert rec.recommended_bid <= POLICY.max_bid(2_000_000, "must_have")

    def test_the_market_value_floor_still_holds(self):
        rows = [WinnerRow(20_000_000, -5.0)] * 40
        curve = WinCurve.from_rows(rows, bands=BANDS, min_sample=30)
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=curve))
        assert rec.recommended_bid >= int(20_000_000 * 1.01)


class TestFallingBack:
    def test_no_curve_means_the_old_stack(self):
        with_curve = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()))
        without = _bid(SmartBidding(ceiling_policy=POLICY))
        assert without.overbid_pct != pytest.approx(with_curve.overbid_pct, abs=0.2)
        assert "curve" not in without.reasoning

    def test_a_curve_with_too_little_evidence_means_the_old_stack(self):
        thin = WinCurve.from_rows([WinnerRow(20_000_000, 5.0)] * 5, bands=BANDS, min_sample=30)
        thin_bid = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=thin))
        stack_bid = _bid(SmartBidding(ceiling_policy=POLICY))
        assert thin_bid.overbid_pct == pytest.approx(stack_bid.overbid_pct, abs=0.01)

    def test_a_thin_band_borrows_the_league_curve_and_says_so(self):
        rows = [WinnerRow(20_000_000, 10.0)] * 5 + [
            WinnerRow(8_000_000, float(i)) for i in range(40)
        ]
        curve = WinCurve.from_rows(rows, bands=BANDS, min_sample=30)
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=curve))
        assert "of 45 league buys" in rec.reasoning

    def test_a_curve_that_raises_falls_back_instead_of_failing_the_bid(self):
        class Broken:
            def premium_at(self, *_a, **_k):
                raise RuntimeError("boom")

        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=Broken()))
        assert rec.recommended_bid > 0
