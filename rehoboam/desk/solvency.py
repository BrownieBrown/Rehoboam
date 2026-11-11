"""Solvency: the only debt rule (spec v3 §5.3 C3/C4, §5.6).

Every check counts open bids as won.

- **The last session before a kickoff:** the cash must cover every open bid.
  A negative budget at kickoff scores zero for the whole matchday.
- **Every earlier session:** the plan may run a debt, as the league's leaders
  do (−36m to −56m between rounds, *Eval* §7.3). The debt is repaid by named
  sales: the bench, meaning the squad minus the eleven it can field without any
  pending bid. The bench is valued at the p10 of each player's drift, so the
  cover is pessimistic. The kickoff eleven is never part of the cover, so the
  debt can never force a starter's sale (I-S2). The debt is also capped at
  `DEBT_CAP_OF_SQUAD_VALUE` of the squad's value, Kickbase's own limit.

The last session's repair (`planner._repair`) sells whatever costs the least U
per euro, which is the bench first.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rehoboam.desk.eleven import best_eleven
from rehoboam.desk.model import Book, Offer, Player

# Kickbase refuses offers past a third of the team's worth
# (ThirtyThreePercentRuleExceeded, CLAUDE.md 2026-10-07).
DEBT_CAP_OF_SQUAD_VALUE = 0.33


def cash_after(book: Book, sells: Iterable[Player], bids: Iterable[Offer]) -> int:
    return (
        book.cash + sum(p.market_value for p in sells) - book.committed - sum(o.bid for o in bids)
    )


def bench_cover(held: Sequence[Player]) -> int:
    """What the bench raises if sold before kickoff, at the p10 of its drift."""
    _, eleven = best_eleven(held)
    starters = {p.player_id for p in eleven}
    return sum(
        round(p.market_value * (1 + min(0.0, p.drift_low)))
        for p in held
        if p.player_id not in starters
    )


def solvent(book: Book, sells: Iterable[Player], bids: Iterable[Offer]) -> bool:
    sells = tuple(sells)
    cash = cash_after(book, sells, bids)
    if cash >= 0:
        return True
    if book.last_before_kickoff:
        return False
    sold = {p.player_id for p in sells}
    held = [p for p in book.held if p.player_id not in sold]
    cap = DEBT_CAP_OF_SQUAD_VALUE * sum(p.market_value for p in held)
    return -cash <= min(cap, bench_cover(held))
