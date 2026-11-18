"""When the bot decides: the trading sessions and the kickoff backstop (UTC).

One home for the live timer (deploy/azure_function/function_app.py, pinned by a test), the
replay (`sim.engine.SESSION_TIMES_UTC`) and the repayment rule (`services/repay_window`).

**22:00, not 20:00 (2026-11-18).** Market values move once a night, at ~22:00 Berlin (21:00
UTC in winter, 20:00 in summer). At 20:00 UTC a bid on a listing that expires overnight has
to guess tonight's move, and Kickbase declines a bid below the value at expiry; at 22:00 the
move is known, and the turn and the rise of last night are seen ten hours before the morning
session. Scored against the league's leaders (ADR-0012), frozen-6 with 08:00 + 22:00 beat
08:00 + 20:00 on every axis: W1 950 vs 928 points a matchday and +31.7m vs +30.0m; day 0
940 vs 908 and +14.4m vs +4.5m; no empty slot in either. A third daily session (15:00)
traded worse on W1 (920), so the extra run is a backstop that trades nothing new.

**The backstop.** An hourly tick runs one extra session `BACKSTOP_LEAD` before each round's
first kickoff: the regular repayment session (the last one before the lock) is otherwise a
single point of failure, and the extra session also sets the lineup on the latest team news.
It is not a regular session, so `next_session` ignores it and the regular session still
repays; the backstop only catches what is left.

Pure: no I/O, no clock.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

SESSION_TIMES_UTC: tuple[time, ...] = (time(8, 0), time(22, 0))

# The backstop runs in the hourly tick that falls in [kickoff - LEAD, kickoff - LEAD + TICK):
# 16:00 for a Friday 19:30 kickoff, 11:00 for a Saturday 14:30 one.
BACKSTOP_LEAD = timedelta(hours=4)
TICK = timedelta(hours=1)


def next_session(at: datetime) -> datetime:
    """The first regular session strictly after ``at`` (timezone-aware)."""
    at = at.astimezone(timezone.utc)
    day = at.date()
    for offset in range(2):
        for t in sorted(SESSION_TIMES_UTC):
            candidate = datetime.combine(day + timedelta(days=offset), t, tzinfo=timezone.utc)
            if candidate > at:
                return candidate
    raise ValueError("SESSION_TIMES_UTC is empty")


def backstop_due(now: datetime, kickoff_at: datetime | None) -> bool:
    """Is this hourly tick the one that runs the extra session before ``kickoff_at``?

    Exactly one tick a kickoff, and never the hour of a regular session (that one runs
    anyway, and two sessions at once would race for the same wallet)."""
    if kickoff_at is None:
        return False
    now = now.astimezone(timezone.utc)
    start = kickoff_at - BACKSTOP_LEAD
    if not start <= now < start + TICK:
        return False
    return all(now.hour != t.hour for t in SESSION_TIMES_UTC)
