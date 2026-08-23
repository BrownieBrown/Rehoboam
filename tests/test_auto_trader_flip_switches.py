"""REH-71: the flip verdict must be honoured from .env, without a deploy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.config import Settings


def test_both_switches_exist():
    assert "enable_flip_buys" in Settings.model_fields
    assert "enable_profit_sells" in Settings.model_fields


# ---------------------------------------------------------------------------
# Fix round 1 added source-inspection gate tests here; fix round 2 deleted
# them (M5). They passed on an inverted gate, on a gate that reads the setting
# and ignores it, and on a gate sitting in unreachable code — they only proved
# the setting name was *mentioned*. The behavioural tests below observe actual
# behaviour and supersede them entirely, for the one task in this plan that
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


def _player(pid: str, position: str, buy_price: int, market_value: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid,
        position=position,
        buy_price=buy_price,
        market_value=market_value,
        first_name="P",
        last_name=pid,
    )


# A 14-man squad chosen so that exactly three players sit outside the
# formation-aware best eleven, and each of them exercises a different branch:
#
#   f2  Forward, +20% — a PROFIT sell. Forwards are not position-saturated
#       (2 of a possible 3 starters), so nothing else can pick him up.
#   g2  Goalkeeper, -3% — DEAD WEIGHT. Three keepers against one startable
#       slot; the loss is accepted to free the squad slot.
#   g3  Goalkeeper, -3% — dead weight, same as g2.
#
# Everyone else is a best-eleven starter and therefore protected. Keeping the
# profit candidate at an unsaturated position is what makes the two branches
# separable: a saturated profit candidate would be sold by the dead-weight
# branch anyway and the test would prove nothing.
_SQUAD = [
    _player("g1", "Goalkeeper", 1_000_000, 1_000_000),
    _player("g2", "Goalkeeper", 1_000_000, 970_000),
    _player("g3", "Goalkeeper", 2_000_000, 1_940_000),
    *[_player(f"d{i}", "Defender", 1_000_000, 1_000_000) for i in range(1, 6)],
    *[_player(f"m{i}", "Midfielder", 1_000_000, 1_000_000) for i in range(1, 5)],
    _player("f1", "Forward", 1_000_000, 1_000_000),
    _player("f2", "Forward", 1_000_000, 1_200_000),
]

_SCORES = {
    "g1": 90.0,
    "g2": 5.0,
    "g3": 4.0,
    "d1": 80.0,
    "d2": 79.0,
    "d3": 78.0,
    "d4": 77.0,
    "d5": 76.0,
    "m1": 70.0,
    "m2": 69.0,
    "m3": 68.0,
    "m4": 67.0,
    "f1": 60.0,
    "f2": 59.0,
}


def _sell_ctx() -> SimpleNamespace:
    """A session context with one queued buy — the dead-weight branch requires
    a buy to be waiting before it will realise a loss to free the slot."""
    buy_rec = SimpleNamespace(
        player=SimpleNamespace(id="x", position="Midfielder"),
        marginal_ep_gain=100.0,
    )
    return SimpleNamespace(
        ep_result={
            "squad_scores": [
                SimpleNamespace(player_id=pid, expected_points=ep) for pid, ep in _SCORES.items()
            ],
            "buy_recs": [buy_rec],
            "trade_pairs": [],
        }
    )


def _sold_names(results) -> set[str]:
    return {r.player_name.split()[-1] for r in results if r.action == "SELL"}


class TestProfitSellPhaseGate:
    """`enable_profit_sells` must gate profit-taking and loss-cutting ONLY.

    The same method also holds the dead-weight release: dumping a
    position-saturated bench player to free a squad slot for a points upgrade.
    That branch serves points, not profit — REH-71 never measured it, so the
    flip switch must not silently disable it (fix round 2, I2).
    """

    @staticmethod
    def _run(trader, *, enabled: bool):
        trader.settings.enable_profit_sells = enabled
        trader.api.get_squad.return_value = list(_SQUAD)
        trader.learner = Mock()
        trader.learner.get_tracked_purchase.return_value = None
        with patch(
            "rehoboam.services.trend_service.TrendService.get_trend",
            return_value=SimpleNamespace(trend_7d_pct=0.0),
        ):
            return trader.run_profit_sell_phase(league=SimpleNamespace(id="L"), ctx=_sell_ctx())

    def test_disabled_still_releases_dead_weight_but_takes_no_profit(self, trader):
        sold = _sold_names(self._run(trader, enabled=False))

        assert sold == {"g2", "g3"}, "dead weight must still be released"
        assert "f2" not in sold, "profit selling was disabled"

    def test_enabled_takes_profit_as_well_as_releasing_dead_weight(self, trader):
        sold = _sold_names(self._run(trader, enabled=True))

        assert sold == {"f2", "g2", "g3"}

    def test_disabled_still_reads_the_squad(self, trader):
        """The old gate early-returned before `get_squad`, which is precisely
        how the dead-weight branch got switched off with it."""
        self._run(trader, enabled=False)

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
            player=SimpleNamespace(
                id="p1", first_name="Test", last_name="Buyer", market_value=1_000_000
            ),
            recommended_bid=1_000_000,
            marginal_ep_gain=10.0,
            reason="test upgrade",
            sell_plan=None,
            score=SimpleNamespace(expected_points=50.0),
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

    def test_ep_buy_path_still_reaches_proposal_when_flip_buys_disabled(self, trader):
        """The gate must disable only the flip block, not the surrounding
        EP-driven buy/trade-pair loop it lives inside — pinning the failure
        mode of a gate that accidentally disables more than the flip block.

        Post-approval-gate (REH-89-ish): the EP buy path no longer executes a
        purchase, it records a proposal via `_propose_buy`. `results` stays
        empty because no trade actually happened.
        """
        trader.settings.enable_flip_buys = False
        self._configure_trader(trader)
        ctx = self._ctx()

        with patch("rehoboam.trader.Trader.find_profit_opportunities") as mock_find:
            results = trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        mock_find.assert_not_called()
        assert results == []
        trader.learner.record_proposal.assert_called_once()
        assert trader.learner.record_proposal.call_args.kwargs["player_id"] == "p1"
        assert trader.learner.record_proposal.call_args.kwargs["bid"] == 1_000_000
