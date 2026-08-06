"""REH-71: the flip verdict must be honoured from .env, without a deploy."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.config import Settings


def test_both_switches_exist():
    assert "enable_flip_buys" in Settings.model_fields
    assert "enable_profit_sells" in Settings.model_fields


def test_the_flip_buy_block_is_gated_on_its_switch():
    source = inspect.getsource(AutoTrader.run_unified_trade_phase)

    assert "enable_flip_buys" in source


def test_the_profit_sell_phase_is_gated_on_its_switch():
    source = inspect.getsource(AutoTrader.run_profit_sell_phase)

    assert "enable_profit_sells" in source


# ---------------------------------------------------------------------------
# Fix round 1: the two source-inspection tests above pass on an inverted
# gate, a gate that reads the setting and ignores it, or a gate sitting in
# unreachable code — they only prove the setting name is *mentioned*. These
# tests observe actual behaviour instead, for the one task in this plan that
# flips live trading defaults.
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(tmp_path, settings, monkeypatch) -> AutoTrader:
    """An AutoTrader wired to a MagicMock API so its calls are directly
    observable, with no real network or DB IO. dry_run=True keeps the
    execution service side-effect-free (no api.buy_player call), so the
    EP-driven buy path can run to completion without extra scaffolding.
    """
    monkeypatch.chdir(tmp_path)  # BidLearner/ActivityFeedLearner default to ./logs
    api = MagicMock()
    return AutoTrader(api=api, settings=settings, dry_run=True)


class TestProfitSellPhaseGate:
    """`run_profit_sell_phase` must return [] AND do no work when disabled.

    Returning [] alone is a weak assertion — an enabled run that finds no
    sell candidates also returns []. The "did no work" half (never even
    fetching the squad) is what actually distinguishes disabled from
    enabled-but-idle.
    """

    def test_disabled_returns_empty_and_never_touches_the_squad(self, trader):
        trader.settings.enable_profit_sells = False

        result = trader.run_profit_sell_phase(league=SimpleNamespace(id="L"), ctx=SimpleNamespace())

        assert result == []
        trader.api.get_squad.assert_not_called()

    def test_enabled_gets_past_the_early_return(self, trader):
        trader.settings.enable_profit_sells = True
        # Empty squad short-circuits the method shortly after — that's fine,
        # we only need proof it got past the disabled-path's early return.
        trader.api.get_squad.return_value = []

        trader.run_profit_sell_phase(league=SimpleNamespace(id="L"), ctx=SimpleNamespace())

        trader.api.get_squad.assert_called_once()


class TestFlipBuyBlockGate:
    """The flip-candidate block inside `run_unified_trade_phase`, gated on
    `enable_flip_buys`, with the surrounding EP-driven buy path proven
    unaffected by the gate.
    """

    @staticmethod
    def _ctx(*, allow_flips: bool = True, max_trades: int = 5) -> EPSessionContext:
        """One affordable plain-buy candidate, an open squad slot (empty
        squad/bids refreshed via the mocked API below), and a matchday
        phase that allows flips — the exact preconditions the flip block
        checks alongside its own switch.
        """
        buy_rec = SimpleNamespace(
            player=SimpleNamespace(id="p1", first_name="Test", last_name="Buyer"),
            recommended_bid=1_000_000,
            marginal_ep_gain=10.0,
            reason="test upgrade",
            sell_plan=None,
        )
        phase = MatchdayPhase(
            days_until_match=5,
            phase="aggressive",
            max_trades=max_trades,
            allow_flips=allow_flips,
            reason="test",
        )
        return EPSessionContext(
            ep_result={"buy_recs": [buy_rec], "trade_pairs": []},
            matchday_phase=phase,
            my_bids=[],
            my_bid_amounts={},
            squad=[],
            current_budget=5_000_000,
            team_value=20_000_000,
            flip_budget=5_000_000,
        )

    @staticmethod
    def _configure_trader(trader) -> None:
        """Wire the mid-phase squad/bid/budget refresh to an empty, open
        squad, and stub the wash-trade learner lookup to a real bool — a
        bare Mock's auto-attribute would return a truthy MagicMock and
        wrongly filter our one candidate out before the gate is even
        reached.
        """
        trader.api.get_squad.return_value = []
        trader.api.get_my_bids.return_value = []
        trader.api.get_team_info.return_value = {
            "budget": 5_000_000,
            "team_value": 20_000_000,
        }
        trader.learner = Mock()
        trader.learner.was_recently_sold.return_value = False

    def test_flip_search_not_called_when_disabled(self, trader):
        trader.settings.enable_flip_buys = False
        self._configure_trader(trader)
        ctx = self._ctx()

        with patch("rehoboam.trader.Trader.find_profit_opportunities") as mock_find:
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        mock_find.assert_not_called()

    def test_flip_search_called_when_enabled_with_open_slot_and_phase_allows(self, trader):
        trader.settings.enable_flip_buys = True
        self._configure_trader(trader)
        ctx = self._ctx(allow_flips=True)

        with patch("rehoboam.trader.Trader.find_profit_opportunities") as mock_find:
            mock_find.return_value = []
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        mock_find.assert_called_once()

    def test_ep_buy_path_still_executes_when_flip_buys_disabled(self, trader):
        """The gate must disable only the flip block, not the surrounding
        EP-driven buy/trade-pair loop it lives inside — pinning the failure
        mode of a gate that accidentally disables more than the flip block.
        """
        trader.settings.enable_flip_buys = False
        self._configure_trader(trader)
        ctx = self._ctx()

        with patch("rehoboam.trader.Trader.find_profit_opportunities") as mock_find:
            results = trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        mock_find.assert_not_called()
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action == "BUY"
