"""Tests for ExecutionService budget-at-kickoff safety guard (REH-11)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rehoboam.services.execution import (
    AutoTradeResult,
    BudgetSafetyError,
    ExecutionService,
)


@pytest.fixture
def player():
    p = MagicMock()
    p.first_name = "Test"
    p.last_name = "Player"
    p.id = "p1"
    return p


@pytest.fixture
def api():
    a = MagicMock()
    a.buy_player = MagicMock(return_value=None)
    return a


@pytest.fixture
def tracker():
    t = MagicMock()
    t.record_bid_placed = MagicMock(return_value=None)
    return t


@pytest.fixture
def service(api, tracker):
    return ExecutionService(api=api, tracker=tracker, dry_run=False)


@pytest.fixture
def dry_service(api, tracker):
    return ExecutionService(api=api, tracker=tracker, dry_run=True)


def test_live_buy_raises_when_insolvent_in_lockout_window(service, player, api):
    """days=0 + budget < price → BudgetSafetyError, no API call."""
    with pytest.raises(BudgetSafetyError) as exc_info:
        service.buy(
            league=MagicMock(),
            player=player,
            price=2_000_000,
            reason="test",
            current_budget=1_000_000,
            days_until_match=0,
        )
    assert "Test Player" in str(exc_info.value)
    assert api.buy_player.call_count == 0


def test_dry_run_returns_failure_result_no_raise(dry_service, player, api, caplog):
    """days=0 + budget < price + dry_run → failure result, no API call, ERROR logged."""
    with caplog.at_level("ERROR"):
        result = dry_service.buy(
            league=MagicMock(),
            player=player,
            price=2_000_000,
            reason="test",
            current_budget=1_000_000,
            days_until_match=0,
        )
    assert isinstance(result, AutoTradeResult)
    assert result.success is False
    assert result.error is not None and "BLOCK" in result.error
    assert api.buy_player.call_count == 0
    assert any("BLOCK" in rec.message for rec in caplog.records)


def test_solvent_buy_in_lockout_window_proceeds(service, player, api, tracker):
    """days=0 + budget > price → guard passes, API called normally."""
    result = service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=10_000_000,
        days_until_match=0,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1
    assert tracker.record_bid_placed.call_count == 1


def test_insolvent_buy_outside_lockout_window_proceeds(service, player, api):
    """days=2 (out of window) + budget < price → guard skipped, API called.

    The guard is matchday-locked logic only — debt-tolerant trading days
    are gated by the upstream phase's flip_budget, not this guard.
    """
    result = service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=1_000_000,
        days_until_match=2,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1


def test_insolvent_buy_unknown_schedule_proceeds(service, player, api):
    """days=None (unknown schedule) → guard skipped, API called.

    Unknown-schedule sessions default to phase=moderate upstream, which
    has its own flip_budget gate. The guard only fires on a positive days
    signal.
    """
    result = service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=1_000_000,
        days_until_match=None,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1


def test_buy_at_zero_post_budget_proceeds(service, player, api):
    """days=1 + budget == price → post-budget exactly 0, guard passes.

    Pins the boundary: '< 0' is strict, '== 0' is allowed.
    """
    result = service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=2_000_000,
        days_until_match=1,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1
