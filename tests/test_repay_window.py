"""The debt is repaid in the last session before the kickoff, not the first one inside the lock.

On Wednesday 2026-11-18 the wallet sat at -22.9m with the MD10 kickoff on Friday 19:30 UTC.
`days_until_match` floors (kickoff - now) to whole days, so the 20:00 session that evening
already counted as "1 day" and the locked-window recovery would have sold Quansah and Andrich
an hour before the nightly market-value update -- with Andrich rising +5.8% a night and a
bid on Mwene open that the recovery withdraws first. Marco: "we should only sell after we get
the player and max out the further rising options." v3 already plans this way (ADR-0003: plan
to the kickoff, execute only what is due now); the legacy recovery now does too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings
from rehoboam.desk.schedule import SESSION_TIMES_UTC
from rehoboam.services.repay_window import SAFETY_MARGIN, repayment_due

UTC = timezone.utc
MD10 = datetime(2026, 11, 20, 19, 30, tzinfo=UTC)  # Friday 20:30 Berlin


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


class TestRepaymentDue:
    @pytest.mark.parametrize(
        ("now", "kickoff", "due"),
        [
            (_at(18, 8), MD10, False),  # moderate phase, two days out
            (_at(18, 20), MD10, False),  # floors to "1 day": the old trigger fired here
            (_at(18, 22), MD10, False),  # Wednesday evening session
            (_at(19, 8), MD10, False),  # Thursday morning: an auction may still settle
            (_at(19, 22), MD10, False),  # Friday 08:00 still runs before the lock
            (_at(20, 8), MD10, True),  # the last regular session before the kickoff
            (_at(20, 16), MD10, True),  # the kickoff backstop catches what is left
            (_at(20, 19, 45), MD10, True),  # past the kickoff (late run): repay at once
            (_at(14, 8), MD10, False),  # six days out: debt until gameday
            # A Friday 21:30 UTC kickoff: the 22:00 session is after it, so 08:00 repays.
            (_at(20, 8), _at(20, 21, 30), True),
            # A Friday 22:30 UTC kickoff: 22:00 would sit inside the margin, 08:00 repays.
            (_at(20, 8), _at(20, 22, 30), True),
            # A Saturday 14:30 UTC kickoff (no Friday game): Saturday 08:00 repays.
            (_at(20, 22), _at(21, 14, 30), False),
            (_at(21, 8), _at(21, 14, 30), True),
        ],
    )
    def test_only_the_last_session_before_the_kickoff_repays(self, now, kickoff, due):
        assert repayment_due(now=now, kickoff_at=kickoff, squad_short=False, fill_due=False) is due

    def test_an_unknown_kickoff_leaves_it_to_the_forced_sell(self):
        assert not repayment_due(now=_at(20, 8), kickoff_at=None, squad_short=False, fill_due=True)

    @pytest.mark.parametrize(
        ("squad_short", "fill_due", "due"),
        [
            (True, True, True),  # the fill needs the cash while its auction can settle
            (True, False, False),  # short, but the trading phases close the slot first
            (False, True, False),  # a full squad waits for the deadline
        ],
    )
    def test_a_short_squad_repays_early_so_the_fill_can_spend(self, squad_short, fill_due, due):
        got = repayment_due(
            now=_at(18, 20), kickoff_at=MD10, squad_short=squad_short, fill_due=fill_due
        )
        assert got is due

    def test_the_margin_is_shorter_than_any_gap_between_sessions(self):
        """The rule reads the live clock (`desk/schedule.py`, pinned to the Azure timer)."""
        hours = sorted(t.hour for t in SESSION_TIMES_UTC)
        gaps = [b - a for a, b in zip(hours, [*hours[1:], hours[0] + 24], strict=True)]
        assert timedelta(0) < SAFETY_MARGIN < timedelta(hours=min(gaps))


def _trader(tmp_path, monkeypatch, *, kickoff_at) -> AutoTrader:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    trader = AutoTrader(api=MagicMock(), settings=Settings(), dry_run=True)
    trader._next_kickoff = SimpleNamespace(at=kickoff_at)
    return trader


def _ctx(days: int | None) -> SimpleNamespace:
    return SimpleNamespace(
        squad=[],
        matchday_phase=SimpleNamespace(days_until_match=days),
        my_bid_amounts={"mwene": 7_533_936},
        current_budget=-22_945_694,
    )


class TestSessionGate:
    """`AutoTrader._debt_recovery_due` is what step 2b asks."""

    def test_wednesday_evening_defers(self, tmp_path, monkeypatch):
        trader = _trader(tmp_path, monkeypatch, kickoff_at=MD10)
        monkeypatch.setattr("rehoboam.auto_trader._emergency_slots_short", lambda squad: 0)
        assert not trader._debt_recovery_due(_ctx(1), now=_at(18, 20))

    def test_friday_morning_repays(self, tmp_path, monkeypatch):
        trader = _trader(tmp_path, monkeypatch, kickoff_at=MD10)
        monkeypatch.setattr("rehoboam.auto_trader._emergency_slots_short", lambda squad: 0)
        assert trader._debt_recovery_due(_ctx(0), now=_at(20, 8))

    def test_a_short_squad_on_the_last_day_repays_now(self, tmp_path, monkeypatch):
        trader = _trader(tmp_path, monkeypatch, kickoff_at=MD10)
        monkeypatch.setattr("rehoboam.auto_trader._emergency_slots_short", lambda squad: 1)
        assert trader._debt_recovery_due(_ctx(1), now=_at(18, 20))

    def test_no_kickoff_known(self, tmp_path, monkeypatch):
        trader = _trader(tmp_path, monkeypatch, kickoff_at=None)
        monkeypatch.setattr("rehoboam.auto_trader._emergency_slots_short", lambda squad: 0)
        assert not trader._debt_recovery_due(_ctx(None), now=_at(20, 8))
