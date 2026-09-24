"""Sell more often when he is not a top player at his position (2026-09-24)."""

from __future__ import annotations

from rehoboam.services.rank_sell import RankSellInput, rank_sell_reason

KW = {"floor": 30, "min_appearances": 2, "min_days_for_starter": 3}


def _inp(**over) -> RankSellInput:
    base = {
        "status": 0,
        "avg_points_rank_pos": 45,
        "ep_rank_pos": 45,
        "appearances": 4,
        "trend_7d_pct": -3.0,
        "in_best_eleven": False,
        "days_until_match": 15,
    }
    base.update(over)
    return RankSellInput(**base)


class TestOutForWeeks:
    def test_status_one_sells_regardless_of_rank(self):
        assert rank_sell_reason(_inp(status=1, avg_points_rank_pos=1), **KW) == (
            "Out for weeks (status 1)"
        )

    def test_status_two_is_not_this_rule(self):
        # Castello Jr., 2026-09-24: status 2, about sixth by points per
        # appearance. Marco's hold.
        assert rank_sell_reason(_inp(status=2, avg_points_rank_pos=6), **KW) is None


class TestOutsideTheTopN:
    def test_below_the_floor_on_both_measures_is_sold(self):
        reason = rank_sell_reason(_inp(avg_points_rank_pos=31, ep_rank_pos=31), **KW)
        assert reason == (
            "Outside the top 30 at position on both measures "
            "(points per appearance 31, expected points 31, 4 apps)"
        )

    def test_on_the_floor_is_held(self):
        assert rank_sell_reason(_inp(avg_points_rank_pos=30, ep_rank_pos=200), **KW) is None
        assert rank_sell_reason(_inp(avg_points_rank_pos=200, ep_rank_pos=30), **KW) is None

    def test_the_model_can_vouch_for_an_unproven_player(self):
        # Burger, 2026-09-24: 44th by points per appearance, 7th by expected
        # points, two days after a 24.5 m buy. A hold.
        assert rank_sell_reason(_inp(avg_points_rank_pos=44, ep_rank_pos=7), **KW) is None

    def test_no_prediction_means_no_sale_on_rank(self):
        assert rank_sell_reason(_inp(avg_points_rank_pos=200, ep_rank_pos=None), **KW) is None

    def test_unknown_rank_is_not_bad_rank(self):
        assert rank_sell_reason(_inp(avg_points_rank_pos=None, appearances=0), **KW) is None

    def test_too_few_appearances_to_trust_the_rank(self):
        assert rank_sell_reason(_inp(avg_points_rank_pos=200, appearances=1), **KW) is None

    def test_a_rebounding_price_defers_the_sale(self):
        assert rank_sell_reason(_inp(trend_7d_pct=1.0), **KW) is None
        assert rank_sell_reason(_inp(trend_7d_pct=0.9), **KW) is not None
        assert rank_sell_reason(_inp(trend_7d_pct=None), **KW) is not None

    def test_a_starter_needs_recovery_time(self):
        assert rank_sell_reason(_inp(in_best_eleven=True, days_until_match=2), **KW) is None
        assert rank_sell_reason(_inp(in_best_eleven=True, days_until_match=None), **KW) is None
        assert rank_sell_reason(_inp(in_best_eleven=True, days_until_match=3), **KW) is not None

    def test_a_bench_player_needs_no_recovery_time(self):
        assert rank_sell_reason(_inp(in_best_eleven=False, days_until_match=0), **KW) is not None


# ---------------------------------------------------------------------------
# The sell phase: a rank sell fires from a short squad, ignores the cost basis
# and leaves a good player who is merely doubtful this week alone.
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock, Mock, patch  # noqa: E402

import pytest  # noqa: E402

from rehoboam.auto_trader import AutoTrader  # noqa: E402
from rehoboam.config import Settings  # noqa: E402


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(tmp_path, settings, monkeypatch) -> AutoTrader:
    monkeypatch.chdir(tmp_path)
    return AutoTrader(api=MagicMock(), settings=settings, dry_run=True)


def _player(pid, position, *, status=0, buy_price=0, market_value=1_000_000):
    return SimpleNamespace(
        id=pid,
        position=position,
        status=status,
        buy_price=buy_price,
        market_value=market_value,
        first_name="P",
        last_name=pid,
    )


def _squad(**over):
    """Nine players, so everyone is in the best eleven."""
    players = [
        _player("g1", "Goalkeeper"),
        *[_player(f"d{i}", "Defender") for i in range(1, 5)],
        *[_player(f"m{i}", "Midfielder") for i in range(1, 4)],
        _player("f1", "Forward"),
    ]
    return [over.get(p.id, p) for p in players]


def _run(trader, squad, ranks, *, days_until_match=15):
    trader.api.get_squad.return_value = list(squad)
    trader.learner = Mock()
    trader.learner.get_tracked_purchase.return_value = None
    trader.learner.get_tracked_purchases.return_value = {}
    trader._league_store = Mock()
    trader._league_store.position_ranks.return_value = ranks
    ctx = SimpleNamespace(
        ep_result={
            "squad_scores": [SimpleNamespace(player_id=p.id, expected_points=50.0) for p in squad],
            "buy_recs": [],
            "trade_pairs": [],
        },
        matchday_phase=SimpleNamespace(days_until_match=days_until_match),
    )
    with patch(
        "rehoboam.services.trend_service.TrendService.get_trend",
        return_value=SimpleNamespace(trend_7d_pct=-3.0),
    ):
        return trader.run_profit_sell_phase(league=SimpleNamespace(id="L"), ctx=ctx)


def _sold(results):
    return {r.player_name.split()[-1] for r in results if r.action == "SELL"}


def _rank(pid, rank, apps=4, ep_rank=None):
    return {
        pid: {
            "player_id": pid,
            "avg_points_rank_pos": rank,
            "ep_rank_pos": rank if ep_rank is None else ep_rank,
            "appearances": apps,
        }
    }


class TestRankSellsInThePhase:
    def test_a_low_ranked_starter_without_a_cost_basis_is_sold_with_recovery_time(self, trader):
        # No buy_price at all: the profit loops cannot see him, the rank sell can.
        assert _sold(_run(trader, _squad(), _rank("d2", 45))) == {"d2"}

    def test_the_same_player_is_held_two_days_before_kickoff(self, trader):
        assert _sold(_run(trader, _squad(), _rank("d2", 45), days_until_match=2)) == set()

    def test_a_top_player_who_is_doubtful_this_week_is_held(self, trader):
        squad = _squad(d2=_player("d2", "Defender", status=2))
        assert _sold(_run(trader, squad, _rank("d2", 6))) == set()

    def test_out_for_weeks_is_sold_whatever_the_rank(self, trader):
        squad = _squad(d2=_player("d2", "Defender", status=1))
        assert _sold(_run(trader, squad, _rank("d2", 1))) == {"d2"}

    def test_the_last_goalkeeper_is_protected(self, trader):
        squad = _squad(g1=_player("g1", "Goalkeeper", status=1))
        assert _sold(_run(trader, squad, _rank("g1", 1))) == set()

    def test_the_switch_turns_it_off(self, trader):
        trader.settings.sell_rank_enabled = False
        assert _sold(_run(trader, _squad(), _rank("d2", 45))) == set()

    def test_a_failing_store_read_sells_nothing_on_rank(self, trader):
        trader.api.get_squad.return_value = _squad()
        trader.learner = Mock()
        trader.learner.get_tracked_purchase.return_value = None
        trader.learner.get_tracked_purchases.return_value = {}
        trader._league_store = Mock()
        trader._league_store.position_ranks.side_effect = RuntimeError("pooler down")
        ctx = SimpleNamespace(
            ep_result={
                "squad_scores": [SimpleNamespace(player_id="g1", expected_points=1.0)],
                "buy_recs": [],
                "trade_pairs": [],
            },
        )
        with patch(
            "rehoboam.services.trend_service.TrendService.get_trend",
            return_value=SimpleNamespace(trend_7d_pct=0.0),
        ):
            results = trader.run_profit_sell_phase(league=SimpleNamespace(id="L"), ctx=ctx)
        assert _sold(results) == set()
