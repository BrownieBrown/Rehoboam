"""The direction of the last nightly move, read before a points buy.

Measured 2026-10-25 on 1,484 league resales (2025-08 → 2026-10), with the move
a bidder could see — the update before the listing's own night:

- under 5m, a player who rose resold at a median +27.4% (79% won), one who fell
  or stood still at -2.5% / 0.0% (43% / 29%), and both scored the same points
  after (30 vs 27) — so a buy that did not rise is refused;
- at 5-15m the value while held moved about +10% for a riser and +0.5-1% for a
  faller, in both seasons — a 9-point gap that becomes a cost in the value bid
  rather than a refusal, because there the points side is not settled;
- at 15m+ resale lost whatever the direction — nothing here, the premium cap is
  the rule there.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.services.ceiling_derivation import WinnerRow
from rehoboam.services.entry_direction import EntryDirectionRule
from rehoboam.services.value_bid import optimal_premium
from rehoboam.services.win_curve import WinCurve

BANDS = (0, 5_000_000, 15_000_000)
B = 255_000.0
RULE = EntryDirectionRule()


def _curve():
    # 15m+: winners paid 4..15 evenly; 5-15m: 2..32; under 5m: 0..40.
    rows = [WinnerRow(20_000_000, 4.0 + i * (11 / 39)) for i in range(40)]
    rows += [WinnerRow(8_000_000, 2.0 + i * (30 / 39)) for i in range(40)]
    rows += [WinnerRow(3_000_000, i * (40 / 39)) for i in range(40)]
    return WinCurve.from_rows(rows, bands=BANDS, min_sample=30)


class TestTheVeto:
    @pytest.mark.parametrize("move", [-2.4, -0.1, 0.0])
    def test_a_cheap_player_who_did_not_rise_is_refused(self, move):
        assert RULE.veto_reason(3_000_000, move) is not None

    def test_a_cheap_riser_is_not(self):
        assert RULE.veto_reason(3_000_000, 0.4) is None

    def test_an_unknown_move_refuses_nothing(self):
        assert RULE.veto_reason(3_000_000, None) is None

    @pytest.mark.parametrize("mv", [5_000_000, 9_000_000, 25_000_000])
    def test_from_five_million_up_there_is_no_veto(self, mv):
        assert RULE.veto_reason(mv, -3.0) is None


class TestTheFallingCost:
    def test_a_mid_band_faller_costs_the_measured_gap(self):
        assert RULE.falling_cost_eur(10_000_000, -1.0) == pytest.approx(900_000)

    def test_a_flat_night_counts_as_not_rising(self):
        assert RULE.falling_cost_eur(10_000_000, 0.0) == pytest.approx(900_000)

    @pytest.mark.parametrize(
        ("mv", "move"),
        [
            (10_000_000, 1.2),  # rose
            (10_000_000, None),  # unknown
            (3_000_000, -1.0),  # the veto band, not the cost band
            (20_000_000, -1.0),  # 15m+: the premium cap's job
        ],
    )
    def test_no_cost_outside_a_known_mid_band_fall(self, mv, move):
        assert RULE.falling_cost_eur(mv, move) == 0.0


class TestTheValueBidTakesTheCost:
    def test_a_resale_cost_lowers_the_premium(self):
        plain = optimal_premium(
            _curve(), market_value=10_000_000, unique_gain_pts=20.0, eur_per_point=B
        )
        costed = optimal_premium(
            _curve(),
            market_value=10_000_000,
            unique_gain_pts=20.0,
            eur_per_point=B,
            resale_drift_eur=-900_000,
        )
        assert costed.premium_pct < plain.premium_pct
        assert costed.value_eur == pytest.approx(B * 20 - 900_000)

    def test_a_cost_above_the_worth_bids_no_premium(self):
        point = optimal_premium(
            _curve(),
            market_value=10_000_000,
            unique_gain_pts=2.0,
            eur_per_point=B,
            resale_drift_eur=-900_000,
        )
        assert point.premium_pct == 0.0


def _bidder(rule=RULE):
    return SmartBidding(win_curve=_curve(), eur_per_point=B, direction_rule=rule)


def _bid(bidder, *, mv, move, urgent=False, gain=40.0):
    return bidder.calculate_ep_bid(
        asking_price=mv,
        market_value=mv,
        expected_points=60.0,
        marginal_ep_gain=gain,
        confidence=0.7,
        current_budget=50_000_000,
        player_id="p1",
        trend_change_pct=2.0,
        urgent=urgent,
        last_move_pct=move,
    )


class TestTheBidder:
    def test_a_cheap_faller_gets_no_bid_and_says_why(self):
        rec = _bid(_bidder(), mv=3_000_000, move=-1.5)
        assert rec.recommended_bid == 0
        assert "did not rise" in rec.reasoning

    def test_an_urgent_buy_is_never_vetoed(self):
        # An empty slot is -100 at kickoff; a falling price does not outweigh it.
        rec = _bid(_bidder(), mv=3_000_000, move=-1.5, urgent=True)
        assert rec.recommended_bid > 0

    def test_a_mid_band_faller_bids_a_lower_premium_than_a_riser(self):
        # A gain whose optimum sits mid-curve, below the 20% tier ceiling —
        # a larger one pins both bids to the ceiling and hides the cost.
        riser = _bid(_bidder(), mv=10_000_000, move=1.5, gain=12.0)
        faller = _bid(_bidder(), mv=10_000_000, move=-1.5, gain=12.0)
        assert 0 < faller.recommended_bid < riser.recommended_bid
        assert "fell last night" in faller.reasoning

    def test_without_the_rule_the_move_changes_nothing(self):
        off = _bidder(rule=None)
        assert _bid(off, mv=3_000_000, move=-1.5).recommended_bid > 0
        assert (
            _bid(off, mv=10_000_000, move=-1.5).recommended_bid
            == _bid(off, mv=10_000_000, move=1.5).recommended_bid
        )


class TestWhatTheTraderPasses:
    """`TrendAnalysis()` without history reports a 0.0 move, which the rule
    would read as "did not rise"; an emergency must field eleven whatever the
    price did. Both pass no move at all."""

    def test_no_history_is_no_signal(self):
        from rehoboam.services.trend_service import TrendAnalysis
        from rehoboam.trader import _last_move

        assert _last_move(TrendAnalysis(), emergency=False) is None

    def test_an_emergency_reads_no_move(self):
        from types import SimpleNamespace

        from rehoboam.trader import _last_move

        trend = SimpleNamespace(has_data=True, trend_1d_pct=-2.0)
        assert _last_move(trend, emergency=True) is None
        assert _last_move(trend, emergency=False) == -2.0

    def test_a_test_double_for_settings_turns_the_rule_off(self):
        from unittest.mock import MagicMock

        from rehoboam.config import Settings
        from rehoboam.trader import _direction_rule

        assert _direction_rule(MagicMock()) is None
        assert isinstance(
            _direction_rule(Settings(KICKBASE_EMAIL="t@e.com", KICKBASE_PASSWORD="x")),
            EntryDirectionRule,
        )
