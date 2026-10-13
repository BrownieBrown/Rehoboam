"""The recommender plans with the phase's debt allowance, not the raw wallet.

2026-10-12 20:00 UTC (session 4acbbbdf98a1): squad 10/15, wallet -10.0m,
allowance 30.8m, three days to kickoff -- and zero offers. The decision
engine judged `price <= budget` against the wallet, so every buy needed a
sell plan covering the price plus the whole debt; eight of ten players were
rank-held, so no plan was viable and every candidate was dropped. The gate
would have let a buy inside the allowance through. `services/budget` is now
the one rule both read.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.config import Settings
from rehoboam.services.budget import buying_power, max_debt, phase_allowance
from rehoboam.trader import Trader

TV = 100_000_000


class TestBuyingPower:
    """Pure rule: when debt is permitted and how open offers are netted."""

    def test_debt_is_permitted_beyond_the_lockout(self):
        p = buying_power(
            wallet=-10_000_000,
            team_value=TV,
            pending_bid_total=0,
            days_until_match=3,
            max_debt_pct=60.0,
            worth_pct=33.0,
        )
        # 60% of TV is 60m; Kickbase's 33% of (TV + wallet) is 29.7m and binds.
        assert p.max_debt == 29_700_000
        assert p.gross == 19_700_000
        assert p.spendable == 19_700_000
        assert p.debt_allowed

    def test_open_offers_are_netted_from_spendable_not_gross(self):
        p = buying_power(
            wallet=-10_000_000,
            team_value=TV,
            pending_bid_total=2_000_000,
            days_until_match=3,
            max_debt_pct=60.0,
            worth_pct=33.0,
        )
        assert p.gross == 19_700_000
        assert p.spendable == 17_700_000

    @pytest.mark.parametrize("days", [0, 1])
    def test_inside_the_lockout_the_wallet_is_the_limit(self, days):
        p = buying_power(
            wallet=-10_000_000,
            team_value=TV,
            pending_bid_total=1_000_000,
            days_until_match=days,
            max_debt_pct=60.0,
            worth_pct=33.0,
        )
        assert p.max_debt == 0
        assert p.gross == -10_000_000
        assert p.spendable == -11_000_000
        assert not p.debt_allowed

    def test_an_unknown_schedule_permits_no_new_debt(self):
        p = buying_power(
            wallet=5_000_000,
            team_value=TV,
            pending_bid_total=0,
            days_until_match=None,
            max_debt_pct=60.0,
            worth_pct=33.0,
        )
        assert p.gross == 5_000_000
        assert not p.debt_allowed

    def test_matches_the_gates_allowance_on_the_same_day(self):
        """The number the recommender plans with is the number the session's
        `BuyGate` reads as `spendable_budget` (`_compute_flip_budget`)."""
        wallet, pending = -10_000_000, 3_000_000
        allowance = max_debt(team_value=TV, budget=wallet, max_debt_pct=60.0, worth_pct=33.0)
        gate = phase_allowance("moderate", wallet, pending, allowance, schedule_known=True)
        p = buying_power(
            wallet=wallet,
            team_value=TV,
            pending_bid_total=pending,
            days_until_match=3,
            max_debt_pct=60.0,
            worth_pct=33.0,
        )
        assert p.spendable == gate


# ---------------------------------------------------------------------------
# Through the pipeline: what the decision engine is handed.
# ---------------------------------------------------------------------------


def _squad_player(pid: str, position: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid,
        position=position,
        first_name="P",
        last_name=pid,
        team_id=f"club-{pid}",
        price=10_000_000,
        market_value=10_000_000,
    )


_FIELDABLE_SQUAD = [
    _squad_player("g1", "Goalkeeper"),
    *[_squad_player(f"d{i}", "Defender") for i in range(1, 6)],
    *[_squad_player(f"m{i}", "Midfielder") for i in range(1, 5)],
    _squad_player("f1", "Forward"),
]


def _trader(tmp_path, monkeypatch, *, budget: int, bids: list | None = None):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    api = MagicMock()
    api.get_squad.return_value = list(_FIELDABLE_SQUAD)
    api.get_my_bids.return_value = list(bids or [])
    api.get_market.return_value = []
    api.get_team_info.return_value = {"budget": budget, "team_value": TV}
    api.get_league_ranking.side_effect = RuntimeError("no network in this test")
    api.get_competition_matchdays.side_effect = RuntimeError("no network in this test")
    learner = MagicMock()
    learner.recent_buy_prices.return_value = [10_800_000] * 9
    return Trader(api=api, settings=Settings(), bid_learner=learner)


def _capture_engine_budgets(monkeypatch) -> dict:
    seen: dict = {}

    def recommend_buys(self, **kwargs):
        seen["buys"] = kwargs["budget"]
        return []

    def build_trade_pairs(self, **kwargs):
        seen["pairs"] = kwargs["budget"]
        return []

    monkeypatch.setattr("rehoboam.scoring.decision.DecisionEngine.recommend_buys", recommend_buys)
    monkeypatch.setattr(
        "rehoboam.scoring.decision.DecisionEngine.build_trade_pairs", build_trade_pairs
    )
    return seen


class TestRecommenderReadsTheAllowance:
    def test_a_negative_wallet_three_days_out_plans_with_the_allowance(self, tmp_path, monkeypatch):
        seen = _capture_engine_budgets(monkeypatch)
        trader = _trader(tmp_path, monkeypatch, budget=-10_000_000)

        result = trader.get_ep_recommendations(SimpleNamespace(id="L"), days_until_match=3)

        assert seen["buys"] == 19_700_000
        assert seen["pairs"] == 19_700_000
        assert result["budget"] == -10_000_000
        assert result["buying_power"] == 19_700_000
        assert result["spendable_budget"] == 19_700_000

    def test_open_offers_come_off_what_the_engine_may_plan_with(self, tmp_path, monkeypatch):
        seen = _capture_engine_budgets(monkeypatch)
        trader = _trader(
            tmp_path,
            monkeypatch,
            budget=-10_000_000,
            bids=[SimpleNamespace(id="x", user_offer_price=2_000_000)],
        )

        result = trader.get_ep_recommendations(SimpleNamespace(id="L"), days_until_match=3)

        assert seen["buys"] == 17_700_000
        assert result["buying_power"] == 19_700_000
        assert result["spendable_budget"] == 17_700_000

    def test_without_pacing_the_bidder_starts_from_the_net_allowance(self, tmp_path, monkeypatch):
        """Pacing nets open offers off the bidder's ceiling itself; with pacing
        off nobody else would, so the ceiling starts net of them."""
        _capture_engine_budgets(monkeypatch)
        trader = _trader(
            tmp_path,
            monkeypatch,
            budget=-10_000_000,
            bids=[SimpleNamespace(id="x", user_offer_price=2_000_000)],
        )
        trader.settings.pacing_enabled = False

        result = trader.get_ep_recommendations(SimpleNamespace(id="L"), days_until_match=3)

        assert result["pacing"] is None
        assert result["buying_power"] == 17_700_000

    def test_a_blank_team_value_permits_no_debt_like_the_gate(self, tmp_path, monkeypatch):
        seen = _capture_engine_budgets(monkeypatch)
        trader = _trader(tmp_path, monkeypatch, budget=-10_000_000)
        trader.api.get_team_info.return_value = {"budget": -10_000_000, "team_value": 0}

        trader.get_ep_recommendations(SimpleNamespace(id="L"), days_until_match=3)

        assert seen["buys"] == -10_000_000

    def test_on_the_last_day_the_engine_sees_the_wallet(self, tmp_path, monkeypatch):
        seen = _capture_engine_budgets(monkeypatch)
        trader = _trader(tmp_path, monkeypatch, budget=-10_000_000)

        result = trader.get_ep_recommendations(SimpleNamespace(id="L"), days_until_match=1)

        assert seen["buys"] == -10_000_000
        assert result["buying_power"] == -10_000_000

    def test_a_malformed_bid_payload_counts_open_offers_as_zero(self, tmp_path, monkeypatch):
        """Best-effort: the pipeline completes and the gate corrects it later."""
        seen = _capture_engine_budgets(monkeypatch)
        trader = _trader(
            tmp_path,
            monkeypatch,
            budget=-10_000_000,
            bids=[SimpleNamespace(id="x", user_offer_price="not-a-number")],
        )

        result = trader.get_ep_recommendations(SimpleNamespace(id="L"), days_until_match=3)

        assert seen["buys"] == 19_700_000
        assert result["spendable_budget"] == 19_700_000


def test_the_trend_recompute_bids_from_the_allowance_not_the_wallet(tmp_path, monkeypatch):
    """`get_ep_recommendations_with_trends` recomputes every bid; if it read
    `budget` (the wallet) the ceiling would collapse to the wallet again and
    undo the first pass."""
    from rehoboam.bidding_strategy import BidRecommendation

    trader = _trader(tmp_path, monkeypatch, budget=-10_000_000)
    buy_rec = SimpleNamespace(
        player=SimpleNamespace(id="p1", price=1_000_000, market_value=1_000_000, offer_count=0),
        score=SimpleNamespace(
            expected_points=50.0,
            is_dgw=False,
            data_quality=SimpleNamespace(games_played=10),
        ),
        marginal_ep_gain=10.0,
        sell_plan=None,
        metadata={},
        recommended_bid=0,
    )
    trader.get_ep_recommendations = MagicMock(
        return_value={
            "buy_recs": [buy_rec],
            "trade_pairs": [],
            "budget": -10_000_000,
            "buying_power": 19_700_000,
            "spendable_budget": 19_700_000,
            "pacing": None,
        }
    )
    trader.trend_service = MagicMock()
    trader.trend_service.get_trend.return_value = SimpleNamespace(
        trend_7d_pct=0.0, trend_14d_pct=0.0, momentum="flat"
    )
    seen: list[int] = []

    def fake_bid(*args, **kwargs):
        seen.append(kwargs["current_budget"])
        return BidRecommendation(
            base_price=1_000_000,
            recommended_bid=1_000_000,
            overbid_amount=0,
            overbid_pct=0.0,
            reasoning="test",
            budget_ceiling=19_700_000,
        )

    trader.bidding.calculate_ep_bid = MagicMock(side_effect=fake_bid)

    trader.get_ep_recommendations_with_trends(SimpleNamespace(id="L"), days_until_match=3)

    assert seen == [19_700_000]
