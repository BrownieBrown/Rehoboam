"""The cost of winning: a bid below must-have never pays past the band's break-even.

`transfer_outcomes` says where paying more stops paying back: above 15m the
median resale turns negative past about +3%, between 5m and 15m past about
+12%, under 5m never (and there the measured premium is inflated by the
player's own rise during the listing, so the curve only ever LOWERS a bid).
A must-have is bought for points and may knowingly pay past it; the board
names the expected resale instead.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.config import Settings
from rehoboam.services.ceiling_derivation import WinnerRow
from rehoboam.services.profit_curve import OutcomeRow, ProfitCurve
from rehoboam.services.win_curve import WinCurve

POLICY = Settings(kickbase_email="test@example.com", kickbase_password="x").bid_ceiling_policy()
BANDS = (0, 5_000_000, 15_000_000)


def _win_curve():
    rows = [WinnerRow(20_000_000, 4.0 + i * (11 / 39)) for i in range(40)]  # p50 9.5, p75 12.25
    rows += [WinnerRow(8_000_000, 2.0 + i * (30 / 39)) for i in range(40)]  # p50 17, p75 24.5
    return WinCurve.from_rows(rows, bands=BANDS, min_sample=30)


def _profit_curve():
    rows = []
    for _ in range(12):
        rows += [OutcomeRow(20_000_000, 1.0, 0.5), OutcomeRow(20_000_000, 4.0, -5.0)]
        rows += [OutcomeRow(20_000_000, 10.0, -9.0), OutcomeRow(20_000_000, 13.0, -12.0)]
        rows += [OutcomeRow(8_000_000, 1.0, 3.0), OutcomeRow(8_000_000, 10.0, 0.5)]
        rows += [OutcomeRow(8_000_000, 13.0, -6.0), OutcomeRow(8_000_000, 25.0, -15.0)]
    return ProfitCurve.from_rows(rows, bands=BANDS)


def _bid(bidder, *, market_value=20_000_000, gain=50.0, trend=0.0):
    return bidder.calculate_ep_bid(
        asking_price=market_value,
        market_value=market_value,
        expected_points=gain,
        marginal_ep_gain=gain,
        confidence=0.9,
        current_budget=500_000_000,
        player_id="p",
        trend_change_pct=trend,
        offer_count=0,
    )


def _bidder(**kw):
    return SmartBidding(
        ceiling_policy=POLICY, win_curve=_win_curve(), profit_curve=_profit_curve(), **kw
    )


class TestTheCapBinds:
    def test_a_strong_upgrade_above_15m_is_capped_at_the_crossing(self):
        rec = _bid(_bidder())  # strong: win curve p50 = 9.5%, break-even 3%
        assert rec.overbid_pct == pytest.approx(3.0, abs=0.2)
        assert "capped at break-even +3.0%" in rec.reasoning
        assert "median resale -5.0% past it" in rec.reasoning

    def test_a_solid_upgrade_between_5m_and_15m_keeps_a_bid_under_the_crossing(self):
        rec = _bid(_bidder(), market_value=8_000_000, gain=30.0)  # solid: p25 = 9.5%, crossing 12%
        assert rec.overbid_pct == pytest.approx(9.5, abs=0.2)
        assert "break-even" not in rec.reasoning

    def test_a_strong_upgrade_between_5m_and_15m_is_capped_at_twelve(self):
        rec = _bid(_bidder(), market_value=8_000_000)  # strong: p50 = 17%, crossing 12%
        assert rec.overbid_pct == pytest.approx(12.0, abs=0.2)

    def test_the_cap_applies_to_the_static_stack_too(self):
        rec = _bid(SmartBidding(ceiling_policy=POLICY, profit_curve=_profit_curve()))
        assert rec.overbid_pct <= 3.0 + 0.2


class TestAMustHaveMayKnowinglyPayPastIt:
    def test_a_must_have_is_not_capped(self):
        rec = _bid(_bidder(), gain=75.0)  # p75 = 12.25%
        assert rec.overbid_pct == pytest.approx(12.25, abs=0.2)
        assert "capped" not in rec.reasoning

    def test_the_board_names_the_expected_resale(self):
        rec = _bid(_bidder(), gain=75.0)  # 12.25% falls in the 12..15 bucket: -12%
        assert "expected resale -12.0% at this premium" in rec.reasoning


class TestNothingElseChanges:
    def test_the_trend_still_applies_after_the_cap(self):
        rec = _bid(_bidder(), trend=-19.4)
        assert rec.overbid_pct == pytest.approx(3.0 * 0.3, abs=0.2)

    def test_without_a_profit_curve_the_win_curve_stands(self):
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_win_curve()))
        assert rec.overbid_pct == pytest.approx(9.5, abs=0.2)

    def test_a_band_with_no_crossing_is_not_capped(self):
        rec = _bid(_bidder(), market_value=2_000_000)  # under 5m: no evidence, no cap
        assert "capped" not in rec.reasoning

    def test_a_profit_curve_that_raises_leaves_the_bid_uncapped(self):
        class Broken:
            def break_even(self, *_a):
                raise RuntimeError("boom")

            def expected_resale_pct(self, *_a):
                raise RuntimeError("boom")

        rec = _bid(
            SmartBidding(ceiling_policy=POLICY, win_curve=_win_curve(), profit_curve=Broken())
        )
        assert rec.overbid_pct == pytest.approx(9.5, abs=0.2)
