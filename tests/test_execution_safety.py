"""Tests for ExecutionService budget-at-kickoff safety guard (REH-11)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rehoboam.services.execution import (
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
