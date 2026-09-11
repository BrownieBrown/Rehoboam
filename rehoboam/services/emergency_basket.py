"""Which players to buy when the squad cannot field a legal eleven.

Pure, like `safety_gate`, so the choice that spends the whole budget in one
session can be tested exhaustively rather than observed in production.

An empty lineup slot costs -100 points at kickoff, every matchday, and that
penalty is per SLOT — it does not care who fills it. Ranking candidates by
expected points and walking the list greedily ignores the term entirely, which
is how the 2026-08-31 session bought three players and left the fourth slot
empty:

    Nadir        ask  2,571,571   bid  2,957,306  (+15%)   EP 35.0
    Ebnoutalib   ask 12,712,298   bid 15,252,298  (+20%)   EP 61.2   fills gap
    El-Faouzi    ask 14,778,365   bid 19,208,365  (+30%)   EP 94.5
    Avdullahu    ask 16,904,943   bid 21,974,943  (+30%)   EP 70.5

Four cheapest asks total 46,967,177 against a 55,485,928 budget and fit. Four
cheapest *bids* total 56,654,430 and miss by 1,168,502. So the basket is sized
on the asking price — the most slots that fit — and the leftover is spent
afterwards as overbid, best players first. The overbid buys a better chance at
one auction; the slot it costs is a certain -100.

**Without `gap_after` the objective is `total_ep + 100 x players_bought`** —
every body counts as a slot, which is the objective written above and the one
this module shipped with. Writing it out rather than hard-coding "more players
always wins" matters at the edges: cardinality dominates exactly while EP
spreads stay under the penalty, and correctly stops dominating when one
exceeds it. Kept for callers that have not computed fieldability.

**With `gap_after` the objective is the slots a basket actually CLOSES**,
then the smallest basket that closes them, then expected points, then the
lower asking price. Counting bodies is only a proxy for counting slots, and on
2026-09-07 the proxy failed: the squad was 1 GK, 6 DEF, 3 MID, 1 FW — ten can
start, one short — and with EUR 2,334,394 to spend the basket bought Stergiou,
a seventh defender, for 2,294,821. The lineup stayed at ten and the -100 was
paid anyway. `gap_after` answers "how many purchases would the squad still
need?", so a defender there closes nothing, size breaks the tie that would
otherwise bundle dead weight with a real closer, and a basket that closes
nothing at all is refused outright: the answer is "nothing", not "Stergiou".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import combinations

#: What an unfilled lineup slot costs at kickoff. The league rule, not a knob.
EMPTY_SLOT_PENALTY = 100.0

#: Above this many candidates, fall back to a greedy walk. Exact enumeration
#: is 2**n; `recommend_buys` returns 8, so the exact path is what actually
#: runs, and the bound only stops a future widening of the pool from hanging
#: a live trading session.
_EXACT_ENUMERATION_LIMIT = 18

#: Given the positions a basket would add, how many purchases the squad would
#: still need. `None` means every player counts as a slot (the pre-2026-09-11
#: objective), kept for callers that have not computed fieldability.
GapAfter = Callable[[Sequence[str]], int]


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


def _rank_key(
    members: tuple[EmergencyCandidate, ...],
    gap_after: GapAfter | None = None,
) -> tuple:
    """Sort key for picking the best basket. Higher is better.

    With ``gap_after`` the slots a basket actually closes come first — an
    emergency fill exists to make an eleven fieldable, and a 150-EP player at
    a saturated position does not — then the SMALLEST basket that closes them,
    then expected points, then lower cost.

    Size has to outrank expected points. A purchase reduces the shortfall by
    at most one, so the smallest basket closing the most slots is exactly the
    set of closers; anything larger is a closer plus dead weight, which scores
    the same ``closed`` and more EP and would otherwise win. That is how a
    seventh defender at EUR 20,000,000 rides along with the midfielder that
    actually made the eleven fieldable.

    ``closed`` is measured against ``gap_after(())`` — the shortfall of the
    squad as it stands — rather than the caller's ``slots_short``, so the key
    is self-consistent with the function that answers it. ``slots_short``
    stays what it always was: the cap on how many players to enumerate.

    Without ``gap_after``, the original ``(value, -cost)``.
    """
    cost = -sum(c.ask for c in members)
    if gap_after is None:
        return (_value(members), cost)
    closed = gap_after(()) - gap_after([c.position for c in members])
    return (closed, -len(members), sum(c.ep for c in members), cost)


def _best_exact(
    candidates: list[EmergencyCandidate],
    slots_short: int,
    budget: int,
    gap_after: GapAfter | None = None,
) -> tuple[EmergencyCandidate, ...]:
    best: tuple[EmergencyCandidate, ...] = ()
    best_key: tuple | None = None
    for size in range(1, min(slots_short, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            if sum(c.ask for c in combo) > budget:
                continue
            key = _rank_key(combo, gap_after)
            if best_key is None or key > best_key:
                best, best_key = combo, key
    if gap_after is not None and best and best_key is not None and best_key[0] <= 0:
        return ()  # closes nothing: not an emergency buy
    return best


def _best_greedy(
    candidates: list[EmergencyCandidate],
    slots_short: int,
    budget: int,
    gap_after: GapAfter | None = None,
) -> tuple[EmergencyCandidate, ...]:
    """Feasibility-preserving greedy, for a pool too large to enumerate.

    Without ``gap_after``: takes candidates in value order but refuses any
    that would leave too little to afford the cheapest remaining fillers —
    the check that keeps cardinality intact.

    With ``gap_after``: one pick at a time, choosing the affordable candidate
    that closes the most slots, then the most expected points, then the
    cheapest; stops as soon as no candidate closes anything. The running gap
    starts at ``gap_after(())`` — what the squad is actually short — rather
    than at ``slots_short``, so the walk and `_rank_key` measure the same
    thing even when a caller's ``slots_short`` disagrees.
    """
    if gap_after is not None:
        chosen: list[EmergencyCandidate] = []
        remaining = budget
        gap = gap_after(())
        while len(chosen) < slots_short and gap > 0:
            pool = [c for c in candidates if c not in chosen and 0 < c.ask <= remaining]
            if not pool:
                break

            def gain(c: EmergencyCandidate, _current_gap: int = gap) -> tuple:
                after = gap_after([x.position for x in chosen] + [c.position])
                return (_current_gap - after, c.ep, -c.ask)

            pick = max(pool, key=gain)
            closed = gain(pick)[0]
            if closed <= 0:
                break
            chosen.append(pick)
            remaining -= pick.ask
            gap -= closed
        return tuple(chosen)

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

    chosen = []
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
    candidates: list[EmergencyCandidate],
    slots_short: int,
    budget: int,
    gap_after: GapAfter | None = None,
) -> list[EmergencyPick]:
    """Choose the basket of buys that scores the most points this matchday.

    Args:
        candidates: Buyable players, already filtered for wash trades and
            existing bids by the caller.
        slots_short: How many players an eleven is missing.
        budget: Money available to commit, in euros.
        gap_after: Optional. Given the positions of a basket, how many
            purchases the squad would still need. When provided, a basket
            is ranked by the slots it closes before anything else, and a
            basket that closes none is rejected — a seventh defender does
            not fix a lineup that already has six.

    Returns:
        The chosen players with the bid to place for each, ask <= bid <=
        max_bid, totalling no more than `budget`. Empty when nothing is
        affordable, or nothing affordable closes a slot.
    """
    if slots_short <= 0 or budget <= 0:
        return []

    affordable = [c for c in candidates if 0 < c.ask <= budget]
    if not affordable:
        return []

    if len(affordable) <= _EXACT_ENUMERATION_LIMIT:
        members = _best_exact(affordable, slots_short, budget, gap_after)
    else:
        members = _best_greedy(affordable, slots_short, budget, gap_after)

    if not members:
        return []
    return _spend_leftover(members, budget)
