"""When the emergency fill is allowed to spend.

Pure, like `safety_gate` and `emergency_basket`: the one question is "is the
squad's shortfall an emergency *today*?", and both places that ask it -- the
recommendation pipeline that relaxes its filters, and the session step that
buys -- have to get the same answer.

On 2026-09-22 08:00 UTC the squad had ten players and the next kickoff was
seventeen days away (the October international break). The session called it
a lineup emergency in the *aggressive* phase, built its candidate pool with
the relaxed emergency filters, and bought Seol at 1,951,380 -- a falling
player unlikely to start -- which Marco cancelled by hand. The fill is the
"buy almost anything" path; an empty slot is -100 at kickoff, and that penalty
is what buys the relaxed filters. It buys nothing seventeen days out, when
the ordinary trading phases can still close the slot with a player who passes
the ordinary bars, at an ordinary bid.

So the emergency is the last day: `days_until_match <= window_days`, the
same window in which the locked phase has stood every other buy path down.
An unknown schedule stays an emergency -- REH-112 exists because on
2026-08-31 a failed fixture lookup left a squad of seven four slots short
with the fill unreachable, and a fail-safe has to fail toward fielding an
eleven.
"""

from __future__ import annotations


def emergency_fill_due(days_until_match: int | None, *, window_days: int) -> bool:
    """Is a squad that cannot field eleven an emergency today?

    True on the last ``window_days`` days before kickoff, and when the
    schedule is unknown. Fieldability itself is the caller's question --
    this only says whether a shortfall may be closed by the emergency path
    now, or must wait for the ordinary trading phases.
    """
    if days_until_match is None:
        return True
    return days_until_match <= window_days
