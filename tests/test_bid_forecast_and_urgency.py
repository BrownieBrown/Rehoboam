"""Two more things a bid considers: tonight's market-value move, and how badly
the slot is needed.

The auction settles after the next nightly update, so a premium is judged
against the value then: a falling forecast lowers the bid to the same premium
on tomorrow's value, a rising one changes nothing (the gate checks today's
ceiling). When the squad cannot field eleven and kickoff is close, every tier
reads the win curve higher up, because an empty slot is -100.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.config import Settings
from rehoboam.services.ceiling_derivation import WinnerRow
from rehoboam.services.win_curve import WinCurve

POLICY = Settings(kickbase_email="test@example.com", kickbase_password="x").bid_ceiling_policy()
BANDS = (0, 5_000_000, 15_000_000)


def _curve():
    rows = [WinnerRow(20_000_000, 4.0 + i * (11 / 39)) for i in range(40)]  # p50 9.5, p75 12.25
    return WinCurve.from_rows(rows, bands=BANDS, min_sample=30)


def _bid(bidder, *, gain=75.0, forecast=None, urgent=False, trend=0.0):
    return bidder.calculate_ep_bid(
        asking_price=20_000_000,
        market_value=20_000_000,
        expected_points=gain,
        marginal_ep_gain=gain,
        confidence=0.9,
        current_budget=500_000_000,
        player_id="p",
        trend_change_pct=trend,
        offer_count=0,
        forecast_change_pct=forecast,
        urgent=urgent,
    )


class TestTheNextUpdate:
    def test_a_falling_forecast_lowers_the_premium_to_tomorrows_value(self):
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()), forecast=-4.0)
        # (1 + 12.25%) x (1 - 4%) - 1 = 7.76%
        assert rec.overbid_pct == pytest.approx((1.1225 * 0.96 - 1) * 100, abs=0.2)
        assert "next update forecast -4.0%" in rec.reasoning

    def test_a_rising_forecast_changes_nothing(self):
        bidder = SmartBidding(ceiling_policy=POLICY, win_curve=_curve())
        assert _bid(bidder, forecast=+3.0).overbid_pct == pytest.approx(_bid(bidder).overbid_pct)
        assert "forecast" not in _bid(bidder, forecast=+3.0).reasoning

    def test_a_fall_larger_than_the_premium_floors_at_the_market_value_floor(self):
        rec = _bid(SmartBidding(ceiling_policy=POLICY, win_curve=_curve()), forecast=-20.0)
        assert rec.recommended_bid == int(20_000_000 * 1.01)

    def test_the_trend_still_applies_after_the_forecast(self):
        rec = _bid(
            SmartBidding(ceiling_policy=POLICY, win_curve=_curve()), forecast=-4.0, trend=-19.4
        )
        assert rec.overbid_pct == pytest.approx((1.1225 * 0.96 - 1) * 100 * 0.3, abs=0.2)

    def test_the_forecast_applies_to_the_static_stack_too(self):
        plain = _bid(SmartBidding(ceiling_policy=POLICY))
        falling = _bid(SmartBidding(ceiling_policy=POLICY), forecast=-4.0)
        assert falling.overbid_pct < plain.overbid_pct


class TestUrgency:
    def test_an_urgent_must_have_reads_the_curve_higher(self):
        bidder = SmartBidding(ceiling_policy=POLICY, win_curve=_curve())
        calm = _bid(bidder)
        urgent = _bid(bidder, urgent=True)  # p90: 4 + 0.9 x 11 = 13.9
        assert urgent.overbid_pct == pytest.approx(4.0 + 0.9 * 11, abs=0.2)
        assert urgent.overbid_pct > calm.overbid_pct
        assert "(urgent)" in urgent.reasoning

    def test_the_bump_is_capped(self):
        bidder = SmartBidding(ceiling_policy=POLICY, win_curve=_curve(), urgency_bump=0.5)
        rec = _bid(bidder, urgent=True)
        assert rec.overbid_pct == pytest.approx(4.0 + 0.95 * 11, abs=0.2)

    def test_urgency_without_a_curve_changes_nothing(self):
        bidder = SmartBidding(ceiling_policy=POLICY)
        assert _bid(bidder, urgent=True).overbid_pct == pytest.approx(_bid(bidder).overbid_pct)
