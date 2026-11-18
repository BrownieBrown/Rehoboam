"""The bot's clock: two regular sessions after the nightly update, one backstop per kickoff."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from rehoboam.desk.schedule import BACKSTOP_LEAD, SESSION_TIMES_UTC, backstop_due, next_session
from rehoboam.sim import engine

UTC = timezone.utc
FUNCTION_APP = Path(__file__).parents[2] / "deploy" / "azure_function" / "function_app.py"


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


class TestNextSession:
    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            (_at(18, 7, 59), _at(18, 8)),
            (_at(18, 8), _at(18, 22)),  # strictly after: a session's own start is not next
            (_at(18, 15), _at(18, 22)),
            (_at(18, 22), _at(19, 8)),
            (_at(18, 23, 30), _at(19, 8)),
        ],
    )
    def test_the_first_regular_session_after(self, now, expected):
        assert next_session(now) == expected

    def test_a_non_utc_clock_is_read_in_utc(self):
        berlin_winter = timezone(timedelta(hours=1))
        assert next_session(datetime(2026, 11, 18, 22, 30, tzinfo=berlin_winter)) == _at(18, 22)


class TestBackstopDue:
    MD10 = _at(20, 19, 30)  # Friday 20:30 Berlin

    @pytest.mark.parametrize(
        ("now", "kickoff", "due"),
        [
            (_at(20, 15), MD10, False),  # before kickoff - 4h
            (_at(20, 16), MD10, True),  # the one tick: 16:00 is in [15:30, 16:30)
            (_at(20, 17), MD10, False),
            (_at(20, 8), MD10, False),  # the regular session repays; not the backstop
            (_at(21, 11), _at(21, 14, 30), True),  # Saturday 15:30 Berlin
            (_at(18, 16), _at(18, 19, 45), True),  # a midweek round
            (_at(20, 16), None, False),  # no kickoff known
            # kickoff - 4h falls on a regular session's hour: that session runs anyway
            (_at(20, 8), _at(20, 12), False),
        ],
    )
    def test_exactly_one_tick_a_kickoff(self, now, kickoff, due):
        assert backstop_due(now, kickoff) is due

    def test_one_tick_in_a_whole_day(self):
        ticks = [_at(20, h) for h in range(24)]
        assert sum(backstop_due(t, self.MD10) for t in ticks) == 1

    def test_the_lead_leaves_time_for_a_sale_and_a_lineup(self):
        assert timedelta(hours=2) <= BACKSTOP_LEAD


class TestOneClock:
    """The live timer, the replay and the repayment rule read the same hours."""

    def test_the_replay_runs_on_the_live_hours(self):
        assert engine.SESSION_TIMES_UTC == SESSION_TIMES_UTC

    def test_the_azure_timer_runs_on_these_hours(self):
        crons = re.findall(r'schedule="0 0 ([0-9,*]+) \* \* \*"', FUNCTION_APP.read_text())
        hours = sorted({t.hour for t in SESSION_TIMES_UTC})
        assert ",".join(str(h) for h in hours) in crons
        assert all(t.minute == 0 for t in SESSION_TIMES_UTC)

    def test_the_backstop_ticks_every_hour(self):
        assert '"0 0 * * * *"' in FUNCTION_APP.read_text()

    def test_the_evening_session_comes_after_the_nightly_update(self):
        """~22:00 Berlin is 21:00 UTC in winter, 20:00 in summer."""
        assert max(SESSION_TIMES_UTC) >= time(22, 0)
