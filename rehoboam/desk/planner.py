"""The planner: one objective instead of ~60 rules (spec v3 §5, ADR-0003).

    U = EP of the best legal eleven
      + μ · (Σ market value × expected drift  −  Σ premiums paid)

It covers the squad the session would end with: held minus sales, plus new
bids counted as won. The search is greedy from "change nothing". At each step
it tries every sale, every bid and every one-for-one swap that keeps the plan
solvent and inside the caps, and takes the best one. It stops when nothing
improves U.

Selling pays exactly market value, so a sale costs nothing but what the
player would have added. A bid costs its premium. There are no exit rules: a
falling bench player is sold because holding him lowers U.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rehoboam.desk import solvency
from rehoboam.desk.eleven import ELEVEN, best_eleven
from rehoboam.desk.model import MAX_SQUAD, Book, Offer, Plan, Player

IMPROVEMENT_EPSILON = 1e-6


@dataclass(frozen=True)
class Limits:
    max_buys: int = 3
    max_sales: int = 3
    max_squad: int = MAX_SQUAD


def utility(players: Sequence[Player], bids: Sequence[Offer], mu: float) -> float:
    ep, _ = best_eleven(players)
    drift_eur = sum(p.market_value * p.drift for p in players)
    premiums = sum(o.premium_eur for o in bids)
    return ep + mu * (drift_eur - premiums)


def plan(book: Book, offers: Sequence[Offer], mu: float, limits: Limits = Limits()) -> Plan:
    sells = _repair(book, mu)
    bids: tuple[Offer, ...] = ()
    current = _score(book, sells, bids, mu)
    while True:
        best = _best_move(book, offers, sells, bids, mu, limits)
        if best is None or best[0] <= current + IMPROVEMENT_EPSILON:
            break
        current, sells, bids = best
    kickoff_ep, _ = best_eleven(_held_after(book, sells))
    return Plan(sells=sells, bids=bids, utility=current, kickoff_ep=kickoff_ep)


def _repair(book: Book, mu: float) -> tuple[Player, ...]:
    """Sales that make an insolvent book solvent, cheapest in U per euro first.

    A negative budget at kickoff scores zero for the matchday, so solvency
    outranks the sale cap. This is the old debt recovery, priced by the same
    objective as every other decision, with no second sacrifice order.
    """
    sells: tuple[Player, ...] = ()
    while solvency.cash_after(book, sells, ()) < 0:
        remaining = _held_after(book, sells)
        if not remaining:
            break
        base = _score(book, sells, (), mu)
        cheapest = min(
            remaining,
            key=lambda p: (base - _score(book, (*sells, p), (), mu)) / max(1, p.market_value),
        )
        sells = (*sells, cheapest)
    return sells


def _squad(book: Book, sells: Sequence[Player], bids: Sequence[Offer]) -> list[Player]:
    return _held_after(book, sells) + [o.player for o in bids]


def _held_after(book: Book, sells: Sequence[Player]) -> list[Player]:
    sold = {p.player_id for p in sells}
    return [p for p in book.held if p.player_id not in sold]


def _score(book: Book, sells: Sequence[Player], bids: Sequence[Offer], mu: float) -> float:
    return utility(_squad(book, sells, bids), bids, mu)


def _best_move(
    book: Book,
    offers: Sequence[Offer],
    sells: tuple[Player, ...],
    bids: tuple[Offer, ...],
    mu: float,
    limits: Limits,
) -> tuple[float, tuple[Player, ...], tuple[Offer, ...]] | None:
    sold = {p.player_id for p in sells}
    bid_on = {o.player.player_id for o in bids}
    sellable = [p for p in book.held if p.player_id not in sold]
    biddable = [o for o in offers if o.player.player_id not in bid_on]

    candidates: list[tuple[tuple[Player, ...], tuple[Offer, ...]]] = []
    if len(sells) < limits.max_sales:
        candidates += [((*sells, p), bids) for p in sellable]
    if len(bids) < limits.max_buys:
        candidates += [(sells, (*bids, o)) for o in biddable]
        if len(sells) < limits.max_sales:
            candidates += [((*sells, p), (*bids, o)) for p in sellable for o in biddable]

    best: tuple[float, tuple[Player, ...], tuple[Offer, ...]] | None = None
    for new_sells, new_bids in candidates:
        if not _feasible(book, new_sells, new_bids, limits):
            continue
        score = _score(book, new_sells, new_bids, mu)
        if best is None or score > best[0]:
            best = (score, new_sells, new_bids)
    return best


def _feasible(book: Book, sells: Sequence[Player], bids: Sequence[Offer], limits: Limits) -> bool:
    size = len(book.held) - len(sells) + book.open_bids + len(bids)
    return (
        size <= limits.max_squad
        and solvency.solvent(book, sells, bids)
        and _fielded(_held_after(book, sells)) >= min(ELEVEN, _fielded(list(book.held)))
    )


def _fielded(players: Sequence[Player]) -> int:
    """How many the eleven can field. C1 counts bids as lost: a sale must never
    leave a hole that only a won auction would fill."""
    return len(best_eleven(players)[1])
