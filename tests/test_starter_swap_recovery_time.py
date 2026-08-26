"""Tests for the recovery-time gate on starter trade-pair swaps (REH-87).

A trade pair sells before it bids. That ordering is forced, not sloppy:
Kickbase counts open bids toward the 15-player cap, so at 15/15 the sell is
what frees the slot the bid needs. The plain-buy path can defer its sell
until the auction resolves; the pair path cannot.

So the sell is unconditional while the buy is only a bid. Losing the auction
leaves the squad a player lighter until a later session replaces him:

* a bench player was scoring nothing, so nothing is lost on the pitch
* a member of the best eleven costs real points every matchday until refilled

The bot runs twice a day, so the risk is only material when there is no time
left to refill before kickoff.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from rehoboam.auto_trader import (
    AutoTrader,
    EPSessionContext,
    MatchdayPhase,
    _starter_swap_has_recovery_time,
)
from rehoboam.config import Settings
from rehoboam.scoring.decision import DecisionEngine
from tests.test_scoring.test_trade_pairs import _make_player, _make_score, _squad


class TestRecoveryTimeGate:
    def test_unknown_match_date_is_treated_as_no_time(self):
        """Fail closed. An unknown date is exactly how this bug hid for months."""
        assert _starter_swap_has_recovery_time(None, min_days=3) is False

    def test_plenty_of_time_allows_the_swap(self):
        assert _starter_swap_has_recovery_time(6, min_days=3) is True

    def test_too_close_to_kickoff_blocks_the_swap(self):
        assert _starter_swap_has_recovery_time(2, min_days=3) is False

    def test_threshold_is_inclusive(self):
        assert _starter_swap_has_recovery_time(3, min_days=3) is True

    def test_matchday_eve_blocks_the_swap(self):
        assert _starter_swap_has_recovery_time(0, min_days=3) is False


class TestTradePairCarriesStarterFlag:
    """build_trade_pairs already knows whether it picked a starter; it must say so."""

    def test_bench_sell_is_not_flagged_as_starter(self):
        engine = DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0)
        squad_players, squad_scores = _squad()
        star = _make_player("mid_star", "Midfielder", price=20_000_000)
        star_score = _make_score("mid_star", 120.0, "Midfielder", price=20_000_000)

        pairs = engine.build_trade_pairs(
            market_scores=[star_score],
            squad_scores=squad_scores,
            roster_context={},
            budget=80_000_000,
            market_players={"mid_star": star},
            squad_players=squad_players,
        )
        assert len(pairs) == 1
        assert pairs[0].sell_is_starter is False

    def test_starter_sell_is_flagged(self):
        engine = DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0)
        squad_players, squad_scores = _squad()

        # Four high-EP defenders claim the four bench targets; the fifth has
        # only a starting defender left to sell.
        scores, players = [], {}
        for i, ep in enumerate([130.0, 130.0, 130.0, 130.0, 120.0]):
            pid = f"buy{i}"
            players[pid] = _make_player(pid, "Defender", price=5_000_000)
            scores.append(_make_score(pid, ep, "Defender", price=5_000_000))

        pairs = engine.build_trade_pairs(
            market_scores=scores,
            squad_scores=squad_scores,
            roster_context={},
            budget=80_000_000,
            market_players=players,
            squad_players=squad_players,
        )
        starter_swaps = [p for p in pairs if p.sell_player.id == "def5"]
        assert len(starter_swaps) == 1
        assert starter_swaps[0].sell_is_starter is True
        assert all(p.sell_is_starter is False for p in pairs if p.sell_player.id != "def5")


class TestGateBehaviourInTradePhase:
    """Observe the gate through `run_unified_trade_phase`, not by inspecting it.

    The sibling flip-switch tests record why: earlier gate tests there were
    deleted because they passed on an inverted gate, on a gate that read the
    setting and ignored it, and on a gate in unreachable code. What matters
    here is whether the irreversible `instant_sell` actually happens.
    """

    @staticmethod
    def _trader(tmp_path, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)
        settings = Settings()
        settings.enable_flip_buys = False  # keep the flip block out of the way
        trader = AutoTrader(api=MagicMock(), settings=settings, dry_run=True)
        # A full squad: 15 players + 0 bids means zero open slots, which is the
        # only condition under which the pair branch runs at all.
        trader.api.get_squad.return_value = [SimpleNamespace(id=f"s{i}") for i in range(15)]
        trader.api.get_my_bids.return_value = []
        trader.api.get_team_info.return_value = {
            "budget": 50_000_000,
            "team_value": 100_000_000,
        }
        trader.learner = Mock()
        trader.learner.was_recently_sold.return_value = False
        return trader

    @staticmethod
    def _ctx(days_until_match, sell_is_starter):
        pair = SimpleNamespace(
            buy_player=SimpleNamespace(
                id="b1",
                first_name="New",
                last_name="Buyer",
                market_value=6_000_000,
                team_id="t1",
            ),
            sell_player=SimpleNamespace(
                id="s1", first_name="Old", last_name="Seller", market_value=5_000_000
            ),
            ep_gain=90.0,
            net_cost=1_000_000,
            recommended_bid=6_000_000,
            sell_is_starter=sell_is_starter,
        )
        phase = MatchdayPhase(
            days_until_match=days_until_match,
            phase="aggressive" if (days_until_match or 0) > 4 else "moderate",
            max_trades=5,
            allow_flips=False,
            reason="test",
        )
        return EPSessionContext(
            # REH-100: the pair pre-flight gate treats an id outside the
            # market data as unspendable, so the buy leg has to be in it.
            ep_result={
                "buy_recs": [],
                "trade_pairs": [pair],
                "market_players": {"b1": pair.buy_player},
            },
            matchday_phase=phase,
            my_bids=[],
            my_bid_amounts={},
            squad=[],
            current_budget=50_000_000,
            team_value=100_000_000,
            flip_budget=50_000_000,
        )

    def _run(self, tmp_path, monkeypatch, days_until_match, sell_is_starter):
        trader = self._trader(tmp_path, monkeypatch)
        with (
            patch.object(trader.execution, "instant_sell") as mock_sell,
            patch.object(trader.execution, "buy") as mock_buy,
        ):
            mock_sell.return_value = SimpleNamespace(success=True, price=5_000_000)
            mock_buy.return_value = SimpleNamespace(success=True)
            trader.run_unified_trade_phase(
                league=SimpleNamespace(id="L"),
                ctx=self._ctx(days_until_match, sell_is_starter),
            )
        return mock_sell

    def test_starter_is_not_sold_on_matchday_eve(self, tmp_path, monkeypatch):
        """The whole point: no irreversible sell we cannot undo before kickoff."""
        mock_sell = self._run(tmp_path, monkeypatch, days_until_match=1, sell_is_starter=True)
        mock_sell.assert_not_called()

    def test_starter_is_not_sold_when_the_match_date_is_unknown(self, tmp_path, monkeypatch):
        mock_sell = self._run(tmp_path, monkeypatch, days_until_match=None, sell_is_starter=True)
        mock_sell.assert_not_called()

    def test_starter_is_sold_when_there_is_time_to_recover(self, tmp_path, monkeypatch):
        mock_sell = self._run(tmp_path, monkeypatch, days_until_match=6, sell_is_starter=True)
        mock_sell.assert_called_once()

    def test_bench_sell_is_unaffected_on_matchday_eve(self, tmp_path, monkeypatch):
        """Bench players cost nothing on the pitch — the gate must not touch them."""
        mock_sell = self._run(tmp_path, monkeypatch, days_until_match=1, sell_is_starter=False)
        mock_sell.assert_called_once()
