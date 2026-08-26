"""Building the pacing context from live session state (REH-85)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.config import Settings
from rehoboam.trader import Trader


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(settings):
    learner = MagicMock()
    learner.recent_buy_prices.return_value = [10_800_000] * 9
    return Trader(api=MagicMock(), settings=settings, bid_learner=learner)


def _bid(amount):
    return SimpleNamespace(user_offer_price=amount)


def test_reserve_covers_every_unfilled_slot(trader):
    """11 players + 1 open bid = 12/15, so 3 slots at EUR 10.8m each."""
    ctx = trader._build_pacing_context(squad_size=11, my_bids=[_bid(40_717_295)])
    assert ctx is not None
    assert ctx.reserve == 32_400_000


def test_open_offers_are_carried_into_the_context(trader):
    ctx = trader._build_pacing_context(squad_size=11, my_bids=[_bid(40_717_295)])
    assert ctx.open_offers == 40_717_295


def test_full_squad_falls_back_to_the_in_season_minimum(trader):
    ctx = trader._build_pacing_context(squad_size=15, my_bids=[])
    assert ctx.reserve == 21_600_000


def test_disabled_setting_returns_no_context(trader, settings):
    settings.pacing_enabled = False
    assert trader._build_pacing_context(squad_size=11, my_bids=[]) is None


def test_a_learner_failure_disables_pacing_rather_than_breaking_the_session(settings):
    """Best-effort learning: a DB problem must never block the EP pipeline."""
    learner = MagicMock()
    learner.recent_buy_prices.side_effect = RuntimeError("db gone")
    t = Trader(api=MagicMock(), settings=settings, bid_learner=learner)
    assert t._build_pacing_context(squad_size=11, my_bids=[]) is None


def test_no_learner_at_all_disables_pacing(settings):
    t = Trader(api=MagicMock(), settings=settings, bid_learner=None)
    assert t._build_pacing_context(squad_size=11, my_bids=[]) is None


def test_the_trend_recompute_does_not_discard_pacing(trader):
    """Production only ever calls get_ep_recommendations_with_trends — both
    live entry points (auto_trader.py, cli.py) go through it, never through
    the plain get_ep_recommendations directly. The trends variant recomputes
    every bid a second time (to fold in trend_change_pct) and overwrites
    recommended_bid. If that recompute forgets to pass `pacing` through, the
    correctly-paced bid from the first pass is silently thrown away and
    pacing does nothing in production, even though the plain pipeline (and
    every other test in this file) looks correct in isolation.
    """
    from rehoboam.bidding_strategy import BidRecommendation
    from rehoboam.services.pacing import PacingContext

    pacing_ctx = PacingContext(reserve=5_000_000, open_offers=0)

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
    pair = SimpleNamespace(
        buy_player=SimpleNamespace(id="p2", price=2_000_000, market_value=2_000_000, offer_count=0),
        sell_player=SimpleNamespace(market_value=1_000_000),
        buy_score=SimpleNamespace(expected_points=40.0, is_dgw=False),
        ep_gain=5.0,
        metadata={},
        recommended_bid=0,
    )

    # Short-circuit the heavy inner pipeline: get_ep_recommendations is
    # exercised by test_reserve_covers_every_unfilled_slot etc. above.
    # This test is only about what get_ep_recommendations_with_trends does
    # with the result once pacing is already computed and attached.
    trader.get_ep_recommendations = MagicMock(
        return_value={
            "buy_recs": [buy_rec],
            "trade_pairs": [pair],
            "budget": 10_000_000,
            "pacing": pacing_ctx,
        }
    )
    trader.trend_service = MagicMock()
    trader.trend_service.get_trend.return_value = SimpleNamespace(
        trend_7d_pct=0.0, trend_14d_pct=0.0, momentum="flat"
    )

    recorded_pacing = []

    def fake_calculate_ep_bid(*args, **kwargs):
        recorded_pacing.append(kwargs.get("pacing"))
        return BidRecommendation(
            base_price=1_000_000,
            recommended_bid=1_000_000,
            overbid_amount=0,
            overbid_pct=0.0,
            reasoning="test",
            budget_ceiling=10_000_000,
        )

    trader.bidding.calculate_ep_bid = MagicMock(side_effect=fake_calculate_ep_bid)

    league = SimpleNamespace(id="league1")
    trader.get_ep_recommendations_with_trends(league)

    # One call for the buy rec, one for the trade pair — both must carry
    # the SAME pacing context that get_ep_recommendations already built,
    # not None (dropped) and not a freshly rebuilt one.
    assert len(recorded_pacing) == 2
    assert all(p is pacing_ctx for p in recorded_pacing)
