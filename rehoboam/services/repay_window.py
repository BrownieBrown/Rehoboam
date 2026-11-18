"""When the locked window repays the wallet: in the last session before the kickoff.

A negative budget at the kickoff is zero points for the whole matchday, so the debt must be
repaid before it. It must not be repaid earlier than that, for two reasons:

- Market values move once a night (~21:00 UTC). A player sold on Wednesday evening is sold at
  Wednesday's value; held to Friday morning, he is sold after two more nightly moves -- on
  2026-11-18 Andrich was rising +5.8% a night and Adeline +4.2%.
- The recovery treats open offers as spent and withdraws the bot's own bids before it sells
  anyone. Repaying while an auction is still open cancels the buy the debt was taken on for.
  Waiting for the last session lets every auction that settles before the kickoff settle
  first: bid, win, then sell.

The trigger used to be `days_until_match <= 1`, and `days_until_match` floors to whole days,
so the first locked session was Wednesday 20:00 for a Friday 19:30 kickoff -- 47.5 hours out.

v3 plans the same way (ADR-0003: plan to the kickoff, execute only what is due now; the flip
book repays in the last session before a kickoff). Pure, so it can be exhaustively tested.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# The trading timer runs at 08:00 and 20:00 UTC (`schedule="0 0 8,20 * * *"` in
# deploy/azure_function/function_app.py). Must equal `rehoboam.sim.engine.SESSION_GAP`
# (a test pins it): the replay and the live session agree on what "the next session" is.
SESSION_GAP = timedelta(hours=12)

# A session due less than this before the kickoff is not trusted to be the one that repays:
# a late timer, a slow run or a Kickbase hiccup would leave the wallet negative at the lock.
SAFETY_MARGIN = timedelta(hours=1)


def repayment_due(
    *,
    now: datetime,
    kickoff_at: datetime | None,
    squad_short: bool,
    fill_due: bool,
) -> bool:
    """Should this session repay the wallet (net of open offers) back to zero?

    True when no later session can be trusted to run before the kickoff: the next one would
    start at or after ``kickoff_at - SAFETY_MARGIN``. Also True when the squad cannot field a
    legal eleven and the emergency fill is due: the fill must spend now, while its auction
    can still settle before the lock, and it cannot spend a negative wallet in the lockout.

    An unknown kickoff is False: step 5's forced sell (`budget.forced_sell_due`) owns that
    case, as it did before.
    """
    if kickoff_at is None:
        return False
    if squad_short and fill_due:
        return True
    return now + SESSION_GAP + SAFETY_MARGIN >= kickoff_at
