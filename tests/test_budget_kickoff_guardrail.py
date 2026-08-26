"""Regression test for the matchday-14 zero-point failure (REH-11).

On matchday 14 last season the bot fielded eleven players worth 1,109 points
and officially scored 0. Cause: negative budget at kickoff, which zeroes the
entire matchday in Kickbase. That single day cost roughly 1,100 points --
more than the gap between 10th and 8th place.

REH-11 added a guard afterwards -- ``ExecutionService.buy()`` in
``rehoboam/services/execution.py`` -- to refuse any purchase that would push
budget below zero within ``LOCKOUT_DAYS`` (1 day) of kickoff. It has never
faced the failure it was built for. This file pins that guard directly
against the real code path (not a guessed API), and documents two known
gaps in its coverage so they are visible and deliberate rather than
accidental:

  * GAP A -- the guard fails open when ``days_until_match`` is unknown
    (``None``), which happens on ordinary API hiccups in
    ``Trader.get_days_until_match``.
  * GAP B -- the guard only blocks purchases made *within* the lockout
    window. A purchase 2-3 days out that leaves budget negative is not
    blocked here at all, and budget does not recover passively -- recovery
    depends entirely on ``Trader.optimize_squad_for_gameday`` selling in
    time.

Both gaps are documented, not fixed, per this task's instructions. Whether
to close them is a decision for the human partner.
"""

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
def live_service(api, tracker):
    return ExecutionService(api=api, tracker=tracker, dry_run=False)


@pytest.fixture
def dry_run_service(api, tracker):
    return ExecutionService(api=api, tracker=tracker, dry_run=True)


# ----------------------------------------------------------------------
# Core behaviour: the matchday-14 scenario
# ----------------------------------------------------------------------


def test_matchday14_buy_refused_in_live_mode(live_service, player, api, permissive_gate):
    """The matchday-14 scenario, live mode: a buy that would push budget
    below zero with the match imminent (days_until_match=1) must be
    refused via BudgetSafetyError, and the API must never be called."""
    with pytest.raises(BudgetSafetyError) as exc_info:
        live_service.buy(
            league=MagicMock(),
            player=player,
            price=5_000_000,
            reason="test",
            current_budget=3_000_000,
            days_until_match=1,
            gate=permissive_gate,
        )
    assert "Test Player" in str(exc_info.value)
    assert api.buy_player.call_count == 0


def test_matchday14_buy_refused_in_dry_run_mode(
    dry_run_service, player, api, caplog, permissive_gate
):
    """The matchday-14 scenario, dry-run mode: same refusal, but surfaced
    as a failed AutoTradeResult instead of a raised exception -- dry-run
    takes a different code path through ExecutionService.buy() and must
    be pinned separately."""
    with caplog.at_level("ERROR"):
        result = dry_run_service.buy(
            league=MagicMock(),
            player=player,
            price=5_000_000,
            reason="test",
            current_budget=3_000_000,
            days_until_match=1,
            gate=permissive_gate,
        )
    assert isinstance(result, AutoTradeResult)
    assert result.success is False
    assert result.error is not None and "BLOCK" in result.error
    assert api.buy_player.call_count == 0
    assert any("BLOCK" in rec.message for rec in caplog.records)


def test_buy_allowed_when_budget_covers_it_at_kickoff(live_service, player, api, permissive_gate):
    """A buy the budget fully covers is allowed even with the match
    imminent (days_until_match=1) -- the guard only fires on insolvency."""
    result = live_service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=3_000_000,
        days_until_match=1,
        gate=permissive_gate,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1


def test_negative_budget_allowed_far_from_kickoff(live_service, player, api, permissive_gate):
    """Going negative several days before kickoff is the intentional
    aggressive strategy (buy good players mid-week, sell to recover before
    the match) -- not a bug. Only the kickoff lockout window matters."""
    result = live_service.buy(
        league=MagicMock(),
        player=player,
        price=5_000_000,
        reason="test",
        current_budget=3_000_000,
        days_until_match=6,
        gate=permissive_gate,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1


def test_exactly_zero_budget_is_acceptable_at_kickoff(live_service, player, api, permissive_gate):
    """Pin the boundary: the penalty triggers on negative budget, not
    zero. A buy that leaves exactly €0 at kickoff must be allowed."""
    result = live_service.buy(
        league=MagicMock(),
        player=player,
        price=3_000_000,
        reason="test",
        current_budget=3_000_000,
        days_until_match=1,
        gate=permissive_gate,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1


# ----------------------------------------------------------------------
# GAP A -- the guard fails open when the match date is unknown
# ----------------------------------------------------------------------


def test_guard_currently_fails_open_when_match_date_unknown(
    live_service, player, api, permissive_gate
):
    """DOCUMENTS A GAP -- does not endorse it.

    ``ExecutionService.buy()``'s guard condition short-circuits on
    ``days_until_match is not None``. ``Trader.get_days_until_match``
    (rehoboam/trader.py:94) returns ``None`` whenever the ``/myeleven``
    response lacks ``nm``/``nextMatch``, or the value is an unexpected
    type -- i.e. an ordinary API hiccup silently disables the 1,100-point
    protection this guard exists to provide.

    This test pins TODAY's behaviour: a buy that goes deeply negative with
    an unknown match date currently proceeds unblocked. It is a
    characterisation test, not a spec for correct behaviour -- whether the
    guard should fail closed instead is a decision for the human partner,
    not something this task changes.
    """
    result = live_service.buy(
        league=MagicMock(),
        player=player,
        price=50_000_000,
        reason="test",
        current_budget=1_000_000,
        days_until_match=None,
        gate=permissive_gate,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1


# ----------------------------------------------------------------------
# GAP B -- the guard blocks creation, not arrival, of negative budget
# ----------------------------------------------------------------------


def test_guard_does_not_prevent_negative_budget_persisting_from_days_out(
    live_service, player, api, permissive_gate
):
    """DOCUMENTS A GAP -- does not endorse it.

    ``LOCKOUT_DAYS = 1`` means this guard only refuses a buy that pushes
    budget negative *within* 24h of kickoff. Budget does not recover
    passively in Kickbase, so a buy made 2-3 days out that leaves budget
    negative is untouched by this guard and persists straight through to
    kickoff. This test pins that: at days_until_match=3, a buy that leaves
    budget deeply negative is permitted by ExecutionService alone.

    This is not a bug in buy() -- it demonstrates why the guard is only
    half the safety net. Recovery from an out-of-window negative balance
    depends entirely on Trader.optimize_squad_for_gameday selling in time
    before kickoff. If that recovery path fails, the matchday-14 failure
    can still recur even though this guard fired zero errors.
    """
    result = live_service.buy(
        league=MagicMock(),
        player=player,
        price=8_000_000,
        reason="test",
        current_budget=3_000_000,
        days_until_match=3,
        gate=permissive_gate,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1
