"""Finding 2: profit flips must be paced too (REH-85 design §3).

Flips are sized by `ProfitTrader` via `Trader.find_profit_opportunities` and
never reach `SmartBidding.calculate_ep_bid` — the reserve `pacing.py`
computes never applied to them, even though the design's §3 table says it
should ("discretionary, and a flip is capital parked rather than deployed").
`enable_flip_buys` defaults to True, so this ran in production: a flip could
consume the exact capital the reserve exists to protect, in the same session
where pacing refused a squad-improvement buy for lack of it.

These tests drive `AutoTrader.run_unified_trade_phase` end to end (with
`execution.buy` mocked out so a real safety-gate/dry-run pass isn't needed)
and assert the pacing cap is applied at the same spot the existing
`opp.buy_price > ctx.flip_budget` affordability check already lives.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.config import Settings
from rehoboam.profit_trader import ProfitOpportunity
from rehoboam.services.execution import AutoTradeResult
from rehoboam.services.pacing import PacingContext


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(tmp_path, settings, monkeypatch) -> AutoTrader:
    monkeypatch.chdir(tmp_path)  # BidLearner/ActivityFeedLearner default to ./logs
    api = MagicMock()
    t = AutoTrader(api=api, settings=settings, dry_run=True)
    t.learner = Mock()
    t.learner.was_recently_sold.return_value = False
    return t


def _squad_player(pid: str, position: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid, position=position, first_name="P", last_name=pid, team_id=f"club-{pid}"
    )


# 14 players (1 slot open under the 15-cap): fieldable on its own, and stays
# fieldable + not dead-weight after adding one more Midfielder (4 -> 5, the
# max any formation can start).
_SQUAD14 = [
    *[_squad_player(f"g{i}", "Goalkeeper") for i in range(1, 4)],
    *[_squad_player(f"d{i}", "Defender") for i in range(1, 6)],
    *[_squad_player(f"m{i}", "Midfielder") for i in range(1, 5)],
    *[_squad_player(f"f{i}", "Forward") for i in range(1, 3)],
]


def _configure(trader):
    trader.api.get_squad.return_value = list(_SQUAD14)
    trader.api.get_my_bids.return_value = []
    trader.api.get_team_info.return_value = {"budget": 20_000_000, "team_value": 40_000_000}
    # Zero the debt allowance so `_compute_flip_budget`'s "aggressive phase"
    # branch (current_budget + max_debt - pending_bids) reduces to exactly
    # current_budget -- keeping the pace_cap arithmetic in each test legible
    # instead of entangled with a second Settings field.
    trader.settings.max_debt_pct_of_team_value = 0.0


def _ctx(pacing_ctx) -> EPSessionContext:
    """One plain-buy candidate priced far beyond any plausible flip_budget, so
    it is skipped in the main loop's affordability check without consuming a
    slot or a bid -- present only so `candidates` is non-empty and the method
    doesn't return before ever reaching the flip search.
    """
    buy_rec = SimpleNamespace(
        player=SimpleNamespace(
            id="p1", first_name="Un", last_name="Affordable", market_value=1_000_000
        ),
        recommended_bid=999_999_999,
        marginal_ep_gain=10.0,
        reason="test upgrade",
        sell_plan=None,
        score=SimpleNamespace(expected_points=50.0),
    )
    phase = MatchdayPhase(
        days_until_match=5, phase="aggressive", max_trades=5, allow_flips=True, reason="test"
    )
    return EPSessionContext(
        ep_result={
            "buy_recs": [buy_rec],
            "trade_pairs": [],
            "pacing": pacing_ctx,
            "market_players": {},
            "competitor_player_ids": set(),
        },
        matchday_phase=phase,
        my_bids=[],
        my_bid_amounts={},
        squad=[],
        current_budget=20_000_000,
        team_value=40_000_000,
        flip_budget=20_000_000,
    )


def _flip_opp(buy_price: int) -> ProfitOpportunity:
    player = SimpleNamespace(
        id="flip1",
        position="Midfielder",
        first_name="Flip",
        last_name="Target",
        team_id="clubF",
        market_value=buy_price,
    )
    return ProfitOpportunity(
        player=player,
        buy_price=buy_price,
        market_value=buy_price,
        value_gap=0,
        value_gap_pct=0.0,
        expected_appreciation=15.0,
        risk_score=10.0,
        hold_days=3,
        reason="test flip",
    )


def _successful_buy(price: int) -> AutoTradeResult:
    return AutoTradeResult(
        success=True,
        player_name="Flip Target",
        action="BUY",
        price=price,
        reason="flip",
        timestamp=0.0,
    )


class TestFlipPacing:
    def test_a_flip_exceeding_the_pacing_cap_is_skipped(self, trader):
        """reserve=15.0m, current_budget=flip_budget=20.0m (no clamp since
        20.0m > 15.0m) -> pace_cap = 20.0m - 15.0m = 5.0m. The flip asks
        8.0m, so it must be skipped."""
        _configure(trader)
        ctx = _ctx(PacingContext(reserve=15_000_000, open_offers=0))
        opp = _flip_opp(buy_price=8_000_000)
        trader.execution.buy = MagicMock()

        with patch("rehoboam.trader.Trader.find_profit_opportunities", return_value=[opp]):
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        trader.execution.buy.assert_not_called()

    def test_an_affordable_flip_still_executes(self, trader):
        """Same reserve, same pace_cap of 5.0m -- a 3.0m flip fits and must
        still go through."""
        _configure(trader)
        ctx = _ctx(PacingContext(reserve=15_000_000, open_offers=0))
        opp = _flip_opp(buy_price=3_000_000)
        trader.execution.buy = MagicMock(return_value=_successful_buy(opp.buy_price))

        with patch("rehoboam.trader.Trader.find_profit_opportunities", return_value=[opp]):
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        trader.execution.buy.assert_called_once()
        call_args = trader.execution.buy.call_args[0]
        assert call_args[1] is opp.player
        assert call_args[2] == opp.buy_price

    def test_no_pacing_context_means_flips_are_not_capped(self, trader):
        """`ep_result["pacing"]` is None when pacing is off entirely
        (`Settings.pacing_enabled=False`, or a learning-side failure). A
        flip that would have exceeded the 5.0m cap from the tests above must
        proceed unimpeded -- pacing being off is not the same as pacing
        refusing everything.
        """
        _configure(trader)
        ctx = _ctx(pacing_ctx=None)
        opp = _flip_opp(buy_price=8_000_000)
        trader.execution.buy = MagicMock(return_value=_successful_buy(opp.buy_price))

        with patch("rehoboam.trader.Trader.find_profit_opportunities", return_value=[opp]):
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        trader.execution.buy.assert_called_once()
