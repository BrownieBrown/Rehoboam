"""A flip is sized against the float, never against debt (2026-10-07).

The Tietz bid: one 19.9m name on a -9.6m wallet, two days before kickoff.
With a measured 68% win rate, a median +10.9% and a 25th-percentile -5.5%,
a fractional-Kelly stake is a modest slice of free capital, spread over a
few names. The float is free cash plus the value already parked in marked
flips; a negative wallet is no float at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rehoboam.services.flip_sizing import flip_size_reason
from tests.test_pacing_flips import (
    _configure,
    _ctx,
    _flip_opp,
    _successful_buy,
    trader,
    settings,
)  # noqa: F401

KW = {"max_fraction": 0.34, "max_open": 3}


class TestTheStake:
    def test_a_flip_within_a_third_of_the_float_is_fine(self):
        assert (
            flip_size_reason(3_000_000, cash=10_000_000, held_flip_value=0, open_flips=0, **KW)
            is None
        )

    def test_a_flip_larger_than_the_fraction_is_refused(self):
        why = flip_size_reason(8_000_000, cash=10_000_000, held_flip_value=0, open_flips=0, **KW)
        assert (
            why == "EUR 8,000,000 is more than 34% of the EUR 10,000,000 float (max EUR 3,400,000)"
        )

    def test_capital_already_in_flips_counts_toward_the_float(self):
        assert (
            flip_size_reason(
                5_000_000, cash=5_000_000, held_flip_value=10_000_000, open_flips=1, **KW
            )
            is None
        )

    def test_a_negative_wallet_is_no_float(self):
        why = flip_size_reason(1_000_000, cash=-9_600_000, held_flip_value=0, open_flips=0, **KW)
        assert why == "no float: the wallet is EUR -9,600,000 and nothing is parked in flips"

    def test_three_open_flips_is_the_limit(self):
        why = flip_size_reason(1_000_000, cash=50_000_000, held_flip_value=0, open_flips=3, **KW)
        assert why == "3 flips already open (max 3)"


class TestTheLoopSizesEveryFlip:
    def test_an_oversized_flip_is_skipped_in_the_trade_phase(self, trader):
        _configure(trader)
        trader.settings.flip_max_fraction_of_float = 0.25  # float 20m -> 5m
        ctx = _ctx(pacing_ctx=None)
        opp = _flip_opp(buy_price=8_000_000)
        trader.execution.buy = MagicMock()
        with patch("rehoboam.trader.Trader.find_profit_opportunities", return_value=[opp]):
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)
        trader.execution.buy.assert_not_called()

    def test_a_flip_within_the_stake_executes(self, trader):
        _configure(trader)
        trader.settings.flip_max_fraction_of_float = 0.25
        ctx = _ctx(pacing_ctx=None)
        opp = _flip_opp(buy_price=4_000_000)
        trader.execution.buy = MagicMock(return_value=_successful_buy(opp.buy_price))
        with patch("rehoboam.trader.Trader.find_profit_opportunities", return_value=[opp]):
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)
        trader.execution.buy.assert_called_once()

    def test_open_flips_in_the_ledger_count(self, trader):
        _configure(trader)
        trader.settings.flip_max_open = 1
        trader.learner.get_tracked_purchases.return_value = {
            "held": {"player_id": "held", "intent": "flip"}
        }
        ctx = _ctx(pacing_ctx=None)
        ctx.squad = [
            SimpleNamespace(
                id="held",
                market_value=2_000_000,
                position="Midfielder",
                first_name="H",
                last_name="eld",
                team_id="c",
            )
        ]
        opp = _flip_opp(buy_price=3_000_000)
        trader.execution.buy = MagicMock()
        with patch("rehoboam.trader.Trader.find_profit_opportunities", return_value=[opp]):
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)
        trader.execution.buy.assert_not_called()
