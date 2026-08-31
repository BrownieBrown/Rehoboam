"""Which players to buy when the squad cannot field a legal eleven.

Pure, like `safety_gate`, so the choice that spends the whole budget in one
session can be tested exhaustively rather than observed in production.

**The objective is `total_ep + 100 x players_bought`.** An empty lineup slot
costs -100 points at kickoff, every matchday, and that penalty is per SLOT —
it does not care who fills it. Ranking candidates by expected points and
walking the list greedily ignores the term entirely, which is how the
2026-08-31 session bought three players and left the fourth slot empty:

    Nadir        ask  2,571,571   bid  2,957,306  (+15%)   EP 35.0
    Ebnoutalib   ask 12,712,298   bid 15,252,298  (+20%)   EP 61.2   fills gap
    El-Faouzi    ask 14,778,365   bid 19,208,365  (+30%)   EP 94.5
    Avdullahu    ask 16,904,943   bid 21,974,943  (+30%)   EP 70.5

Four cheapest asks total 46,967,177 against a 55,485,928 budget and fit. Four
cheapest *bids* total 56,654,430 and miss by 1,168,502. So the basket is sized
on the asking price — the most slots that fit — and the leftover is spent
afterwards as overbid, best players first. The overbid buys a better chance at
one auction; the slot it costs is a certain -100.

Writing the objective out rather than hard-coding "more players always wins"
matters at the edges: cardinality dominates exactly while EP spreads stay
under the penalty, and correctly stops dominating when one exceeds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

#: What an unfilled lineup slot costs at kickoff. The league rule, not a knob.
EMPTY_SLOT_PENALTY = 100.0

#: Above this many candidates, fall back to a greedy walk. Exact enumeration
#: is 2**n; `recommend_buys` returns 8, so the exact path is what actually
#: runs, and the bound only stops a future widening of the pool from hanging
#: a live trading session.
_EXACT_ENUMERATION_LIMIT = 18


@dataclass(frozen=True)
class EmergencyCandidate:
    """One buyable player, priced two ways.

    `ask` is what the listing costs — the floor, since a bid below it cannot
    win. `max_bid` is what the pacing/bidding stack sized for this player,
    which is the ceiling the safety gate will accept.
    """

    id: str
    name: str
    ask: int
    max_bid: int
    ep: float
    fills_gap: bool = False
    position: str = ""


@dataclass(frozen=True)
class EmergencyPick:
    """A chosen candidate and the bid to place for them."""

    candidate: EmergencyCandidate
    bid: int


@dataclass(frozen=True)
class _Basket:
    members: tuple[EmergencyCandidate, ...]
    value: float = field(compare=False, default=0.0)


def _priority(c: EmergencyCandidate) -> float:
    """What one player contributes to `_value`, for ordering one at a time.

    Shared by the greedy fallback and the overbid distribution so neither can
    disagree with the selector about what a pick is worth. A gap filler counts
    the penalty twice for the reason `_value` gives.
    """
    return c.ep + EMPTY_SLOT_PENALTY + (EMPTY_SLOT_PENALTY if c.fills_gap else 0.0)


def _value(members: tuple[EmergencyCandidate, ...]) -> float:
    """Expected points delivered: their scoring, plus the penalties avoided.

    Covering a position below its formation minimum counts as a slot in its
    own right. A position gap is what makes an eleven *illegal* rather than
    merely weaker — eleven players and no goalkeeper still fields ten — so a
    gap filler earns the penalty twice: once for the body, once for making
    the slot fillable at all. Without that term a higher-EP surplus defender
    outranks the only available forward, which is how a squad reaches eleven
    bodies and still takes -100.
    """
    ep = sum(c.ep for c in members)
    slots = EMPTY_SLOT_PENALTY * len(members)
    gaps = EMPTY_SLOT_PENALTY * len({c.position for c in members if c.fills_gap})
    return ep + slots + gaps


def _rank_key(members: tuple[EmergencyCandidate, ...]) -> tuple:
    """Sort key for picking the best basket. Higher is better.

    Cost breaks ties so an equal-value basket does not spend more than it
    needs to — the remainder is another player's bid.
    """
    return (_value(members), -sum(c.ask for c in members))


def _best_exact(
    candidates: list[EmergencyCandidate], slots_short: int, budget: int
) -> tuple[EmergencyCandidate, ...]:
    best: tuple[EmergencyCandidate, ...] = ()
    for size in range(1, min(slots_short, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            if sum(c.ask for c in combo) > budget:
                continue
            if not best or _rank_key(combo) > _rank_key(best):
                best = combo
    return best


def _best_greedy(
    candidates: list[EmergencyCandidate], slots_short: int, budget: int
) -> tuple[EmergencyCandidate, ...]:
    """Feasibility-preserving greedy, for a pool too large to enumerate.

    Takes candidates in value order but refuses any that would leave too
    little to afford the cheapest remaining fillers — the check that keeps
    cardinality intact, and precisely what the plain greedy walk lacked.
    """
    order = sorted(candidates, key=lambda c: (-_priority(c), c.ask))
    by_price = sorted(candidates, key=lambda c: c.ask)

    target = 0
    running = 0
    for c in by_price:
        if target >= slots_short:
            break
        if running + c.ask > budget:
            break
        running += c.ask
        target += 1

    chosen: list[EmergencyCandidate] = []
    remaining = budget
    for c in order:
        if len(chosen) >= target:
            break
        still_needed = target - len(chosen) - 1
        pool = [x for x in by_price if x.id != c.id and x not in chosen]
        cheapest = sum(x.ask for x in pool[:still_needed])
        if c.ask + cheapest <= remaining:
            chosen.append(c)
            remaining -= c.ask
    return tuple(chosen)


def _spend_leftover(members: tuple[EmergencyCandidate, ...], budget: int) -> list[EmergencyPick]:
    """Raise bids from ask toward `max_bid`, best players first.

    Bidding the whole basket at bare ask would forfeit winnable auctions, and
    an auction lost leaves the slot empty anyway. Spent in `_priority` order so
    the players most worth holding get the best chance — gap fillers first,
    since losing one leaves a formation that cannot legally be fielded.
    """
    leftover = budget - sum(c.ask for c in members)
    picks: dict[str, int] = {c.id: c.ask for c in members}
    for c in sorted(members, key=lambda c: -_priority(c)):
        if leftover <= 0:
            break
        headroom = max(0, c.max_bid - c.ask)
        spend = min(headroom, leftover)
        picks[c.id] += spend
        leftover -= spend
    return [EmergencyPick(candidate=c, bid=picks[c.id]) for c in members]


def select_emergency_basket(
    candidates: list[EmergencyCandidate], slots_short: int, budget: int
) -> list[EmergencyPick]:
    """Choose the basket of buys that scores the most points this matchday.

    Args:
        candidates: Buyable players, already filtered for wash trades and
            existing bids by the caller.
        slots_short: How many players an eleven is missing.
        budget: Money available to commit, in euros.

    Returns:
        The chosen players with the bid to place for each, ask <= bid <=
        max_bid, totalling no more than `budget`. Empty when nothing is
        affordable.
    """
    if slots_short <= 0 or budget <= 0:
        return []

    affordable = [c for c in candidates if 0 < c.ask <= budget]
    if not affordable:
        return []

    if len(affordable) <= _EXACT_ENUMERATION_LIMIT:
        members = _best_exact(affordable, slots_short, budget)
    else:
        members = _best_greedy(affordable, slots_short, budget)

    if not members:
        return []
    return _spend_leftover(members, budget)
