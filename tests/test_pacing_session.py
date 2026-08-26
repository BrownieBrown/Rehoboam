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
