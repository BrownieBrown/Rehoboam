"""Marked flips are always traded, whatever the squad looks like around them.

Before this, the profit-sell loop skipped every player in the best eleven,
and a nine-man squad IS the best eleven — so a flip bought into a short
squad could never be sold, and the flip buy guard refused to open one in
the first place (nine plus one cannot field eleven). The purchase record
now says why a player was bought, and the sell loop reads it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings
from rehoboam.learning.tracker import FlipIntent
from rehoboam.services.execution import ExecutionService


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(tmp_path, settings, monkeypatch) -> AutoTrader:
    monkeypatch.chdir(tmp_path)
    return AutoTrader(api=MagicMock(), settings=settings, dry_run=True)


def _player(pid: str, position: str, buy_price: int, market_value: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid,
        position=position,
        buy_price=buy_price,
        market_value=market_value,
        first_name="P",
        last_name=pid,
    )


# Nine players: one keeper, four defenders, three midfielders, one forward.
# Every one of them is in the "best eleven", which is the whole point.
def _short_squad(flip_mv: int) -> list[SimpleNamespace]:
    return [
        _player("g1", "Goalkeeper", 1_000_000, 1_000_000),
        *[_player(f"d{i}", "Defender", 1_000_000, 1_000_000) for i in range(1, 5)],
        _player("m1", "Midfielder", 1_000_000, 1_000_000),
        _player("m2", "Midfielder", 1_000_000, 1_000_000),
        _player("flip", "Midfielder", 1_000_000, flip_mv),
        _player("f1", "Forward", 1_000_000, 1_000_000),
    ]


def _ctx(squad) -> SimpleNamespace:
    return SimpleNamespace(
        ep_result={
            "squad_scores": [SimpleNamespace(player_id=p.id, expected_points=50.0) for p in squad],
            "buy_recs": [],
            "trade_pairs": [],
        }
    )


def _sold(results) -> set[str]:
    return {r.player_name.split()[-1] for r in results if r.action == "SELL"}


def _run(trader, squad, *, flip_row: dict | None, trend_7d: float = 0.0):
    trader.settings.enable_profit_sells = True
    trader.api.get_squad.return_value = list(squad)
    trader.learner = Mock()
    trader.learner.get_tracked_purchase.return_value = flip_row
    trader.learner.get_tracked_purchases.return_value = {"flip": flip_row} if flip_row else {}
    with patch(
        "rehoboam.services.trend_service.TrendService.get_trend",
        return_value=SimpleNamespace(trend_7d_pct=trend_7d),
    ):
        return trader.run_profit_sell_phase(league=SimpleNamespace(id="L"), ctx=_ctx(squad))


def _flip_row(*, days_held: float = 2.0, max_hold_days: int = 5) -> dict:
    import time

    return {
        "player_id": "flip",
        "player_name": "P flip",
        "buy_price": 1_000_000,
        "buy_date": time.time() - days_held * 86_400,
        "source": "real",
        "intent": "flip",
        "target_pct": 12.0,
        "max_hold_days": max_hold_days,
    }


class TestMarkedFlipsAreSoldFromAShortSquad:
    def test_a_flip_at_target_is_sold_even_though_it_is_in_the_best_eleven(self, trader):
        # +12% on a flat trend: the trend-adjusted target is 10%.
        assert _sold(_run(trader, _short_squad(1_120_000), flip_row=_flip_row())) == {"flip"}

    def test_the_same_player_without_the_mark_is_protected_as_a_starter(self, trader):
        assert _sold(_run(trader, _short_squad(1_120_000), flip_row=None)) == set()

    def test_a_flip_below_target_is_held(self, trader):
        assert _sold(_run(trader, _short_squad(1_050_000), flip_row=_flip_row())) == set()

    def test_a_flip_past_the_stop_loss_is_sold_without_a_replacement_queued(self, trader):
        # -16% against max_loss_pct -15, no rebound.
        assert _sold(_run(trader, _short_squad(840_000), flip_row=_flip_row())) == {"flip"}

    def test_a_flip_past_the_stop_loss_but_rebounding_is_held(self, trader):
        results = _run(trader, _short_squad(840_000), flip_row=_flip_row(), trend_7d=2.0)
        assert _sold(results) == set()

    def test_an_expired_hold_alone_does_not_sell(self, trader):
        # Marco's rule (2026-09-24): hold until the trend-adjusted target or
        # the stop-loss fires; the hold limit is a report, not a trigger.
        row = _flip_row(days_held=9.0, max_hold_days=5)
        assert _sold(_run(trader, _short_squad(1_050_000), flip_row=row)) == set()

    def test_a_flip_just_bought_is_held_by_the_hold_period_guard(self, trader):
        row = _flip_row(days_held=0.1)
        assert _sold(_run(trader, _short_squad(1_120_000), flip_row=row)) == set()

    def test_a_flip_at_its_position_minimum_is_not_sold(self, trader):
        squad = [p for p in _short_squad(1_120_000) if p.id != "f1"]
        squad[-1] = _player("flip", "Forward", 1_000_000, 1_120_000)
        assert _sold(_run(trader, squad, flip_row=_flip_row())) == set()


class TestTheFlipBuyPassesItsIntentToTheTracker:
    def test_execution_records_the_flip_intent_on_success(self):
        api = MagicMock()
        tracker = MagicMock()
        service = ExecutionService(api=api, tracker=tracker, dry_run=False)
        player = SimpleNamespace(id="p1", first_name="T", last_name="F", price=1, market_value=1)
        gate = MagicMock()
        gate.check.return_value = SimpleNamespace(ok=True, reasons=[])
        gate.tier = None

        service.buy(
            league=MagicMock(),
            player=player,
            price=1_000_000,
            reason="Flip: +12% in 5d",
            current_budget=5_000_000,
            days_until_match=10,
            gate=gate,
            flip=FlipIntent(target_pct=12.0, max_hold_days=5),
        )

        kwargs = tracker.record_bid_placed.call_args.kwargs
        assert kwargs["flip"] == FlipIntent(target_pct=12.0, max_hold_days=5)


class TestTheFlipBuyGuardIsRelative:
    """Nine plus one cannot field eleven, and that is not a reason to refuse
    the flip: adding a player never makes fieldability worse. The guard now
    compares against today's squad instead of against eleven."""

    def test_a_short_squad_may_open_a_flip(self):
        from rehoboam.auto_trader import _flip_worsens_fieldability

        squad = _short_squad(1_000_000)
        assert _flip_worsens_fieldability(squad, _player("x", "Forward", 1, 1)) is False

    def test_a_full_squad_that_could_field_eleven_still_may(self):
        from rehoboam.auto_trader import _flip_worsens_fieldability

        squad = _short_squad(1_000_000) + [
            _player("m3", "Midfielder", 1, 1),
            _player("f2", "Forward", 1, 1),
        ]
        assert _flip_worsens_fieldability(squad, _player("x", "Forward", 1, 1)) is False
