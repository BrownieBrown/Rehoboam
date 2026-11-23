"""The kickoff plan: one desired state for the next kickoff, re-derived every session.

The two books (ADR-0008) decided buys and repayment in different places: the core bid
for its eleven on the debt allowance, and the last session before the kickoff repaid
whatever debt it found by selling the cheapest points. Each rule passed its own check
and together they bought a starter on Monday that Friday sold again (M4-2; the live
dry run of 2026-11-13: −€52.0m owed against €46.9m of non-core sales).

ADR-0003 had the right shape, a desired state with its sales named, and churned
because it valued seven days of drift for every player every 12 hours. This module
keeps the shape and drops the cause: drift moves money only for the players the plan
keeps for points, and everything it does not keep follows one exit rule outside it.

**T** is the eleven the plan wants at the next kickoff: chosen from the squad (open
bids counted as won), and listings that settle before the kickoff, to maximise

    U = EP(best legal eleven of T) + μ · (Σ_T value × drift − Σ_buys premium)

subject to the **kickoff budget**

    K = cash − Σ_buys bid + Σ_{squad outside T} exit value ≥ 0

where a player's exit value is what he raises by the last session at the p10 of his
drift (zero for a bid that settles too late to sell). Everyone outside T is trading
stock, sold at the turn or by the last session, so K ≥ 0 is the promise that the
wallet can be brought to zero without touching T. The flip entries spend the same K
after T, so nothing is bought that the kickoff cannot repay.

Pure: frozen inputs, no I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from rehoboam.desk.eleven import ELEVEN, best_eleven
from rehoboam.desk.model import Offer, Player

# Expected points a matchday a change to T must add, unless it fills an empty slot.
# The scorer's error is about this size; a smaller gain buys churn (M2-13, core.py).
MIN_GAIN = 10.0
REPAIR_MAX_SET = 3  # sales a repair names together, as the legacy recovery did
SWAP_MAX_SALES = 2  # sales a buy may name to pay for itself


@dataclass(frozen=True)
class KickoffPlan:
    keep: tuple[Player, ...]  # T: the eleven at the next kickoff
    buys: tuple[Offer, ...]  # this session's bids for T
    stock: tuple[Player, ...]  # the squad outside T: held for drift, sold by the exit rule
    budget: float  # K: the kickoff wallet if all stock is sold at its exit value
    utility: float  # U of T, in points
    repaired: bool = False  # T had to give up players to make K non-negative
    # Squad players T gave up for the budget (a repair, or a buy's named sales): due
    # at the last session before the kickoff, not before (ADR-0003: execute only
    # what is due now; a flip that rises may yet pay it).
    named: tuple[Player, ...] = ()


@dataclass(frozen=True)
class _Eval:
    utility: float
    ep: float
    eleven: tuple[Player, ...]
    budget: float


def plan(
    squad: Sequence[Player],
    offers: Sequence[Offer],
    mu: float,
    *,
    cash: int,
    exit_value: Mapping[str, float],
    fieldable: Callable[[Player], bool] = lambda p: True,
    slots: int,
    allowance: float,
    max_upgrades: int | None = None,
    min_gain: float = MIN_GAIN,
    capital_neutral: bool = False,
) -> KickoffPlan:
    """The kickoff squad T and the bids that build it.

    ``squad``: every player held, and every open bid's player (counted as won).
    ``offers``: listings that settle before the kickoff. ``cash``: the wallet net of
    open bids. ``exit_value``: per squad player, what he raises by the last session.
    ``fieldable``: whether a squad player can play the kickoff (an open bid that
    settles after it cannot). ``slots``: free squad places for new bids.
    ``allowance``: Kickbase's debt cap, which the wallet may not pass even in
    transit (bids are placed before the sales that pay for them).
    ``max_upgrades``: buys that fill no empty slot, at most this many a session
    (None: no limit). Every pick on a noisy EP is a draw on the winner's curse.
    ``capital_neutral``: an upgrade's bid is at most what the starters it moves out
    of T are worth, plus the wallet's positive cash (ADR-0010, ADR-0013). Measured
    2026-11-13: without it the plan sold starters who scored 95 a match for buys
    who scored 82 (EP said otherwise), 120 points a matchday below frozen-6.
    """
    ctx = _Ctx(tuple(squad), mu, cash, exit_value, fieldable)
    removed: frozenset[str] = frozenset()
    buys: tuple[Offer, ...] = ()
    current = ctx.evaluate(removed, buys)
    repaired = current.budget < 0
    if repaired:
        removed = _repair(ctx, current)
        current = ctx.evaluate(removed, buys)
    pool = list(offers)
    upgrades = 0
    while pool and len(buys) < slots:
        holes_only = max_upgrades is not None and upgrades >= max_upgrades
        move = _best_move(
            ctx, removed, buys, current, pool, allowance, min_gain, holes_only, capital_neutral
        )
        if move is None:
            break
        if len(move[2].eleven) <= len(current.eleven):
            upgrades += 1
        removed, buys, current = move
        bought = _ids(o.player for o in buys)
        pool = [o for o in pool if o.player.player_id not in bought]
    kept = _ids(current.eleven)
    return KickoffPlan(
        keep=current.eleven,
        buys=buys,
        stock=tuple(p for p in squad if p.player_id not in kept),
        budget=current.budget,
        utility=current.utility,
        repaired=repaired,
        named=tuple(p for p in squad if p.player_id in removed),
    )


def _ids(players) -> frozenset[str]:
    return frozenset(p.player_id for p in players)


@dataclass(frozen=True)
class _Ctx:
    squad: tuple[Player, ...]
    mu: float
    cash: int
    exit_value: Mapping[str, float]
    fieldable: Callable[[Player], bool]

    def evaluate(self, removed: frozenset[str], buys: Sequence[Offer]) -> _Eval:
        pool = [p for p in self.squad if p.player_id not in removed and self.fieldable(p)]
        ep, eleven = best_eleven([*pool, *(o.player for o in buys)])
        kept = _ids(eleven)
        drift = sum(p.market_value * p.drift for p in eleven)
        premiums = sum(o.premium_eur for o in buys)
        budget = (
            self.cash
            - sum(o.bid for o in buys)
            + sum(
                self.exit_value.get(p.player_id, 0.0) for p in self.squad if p.player_id not in kept
            )
        )
        return _Eval(ep + self.mu * (drift - premiums), ep, eleven, budget)


def _repair(ctx: _Ctx, base: _Eval) -> frozenset[str]:
    """The fewest-points set of T's players to give up so that K >= 0.

    Up to `REPAIR_MAX_SET` together, scored on their own eleven (points lost are
    not additive across formations); ties: fewer sales, then the least to spare.
    If no such set covers it, give up the least points per euro one at a time."""
    best: tuple[tuple[float, int, float], frozenset[str]] | None = None
    for size in range(1, REPAIR_MAX_SET + 1):
        for group in combinations(base.eleven, size):
            gone = _ids(group)
            e = ctx.evaluate(gone, ())
            if e.budget < 0:
                continue
            key = (base.utility - e.utility, size, e.budget)
            if best is None or key < best[0]:
                best = (key, gone)
    if best is not None:
        return best[1]
    removed: frozenset[str] = frozenset()
    current = base
    while current.budget < 0 and current.eleven:
        now = current

        def rate(p: Player, now: _Eval = now, gone: frozenset[str] = removed) -> tuple:
            after = ctx.evaluate(gone | {p.player_id}, ())
            return (
                (now.utility - after.utility) / max(1.0, after.budget - now.budget),
                p.player_id,
            )

        removed = removed | {min(now.eleven, key=rate).player_id}
        current = ctx.evaluate(removed, ())
    return removed


def _reserve(pool: Sequence[Offer], chosen: frozenset[str], holes: int) -> int:
    """The cheapest bids that could still fill ``holes`` empty slots."""
    if holes <= 0:
        return 0
    rest = sorted(o.bid for o in pool if o.player.player_id not in chosen)
    return sum(rest[:holes])


def _best_move(
    ctx: _Ctx,
    removed: frozenset[str],
    buys: tuple[Offer, ...],
    now: _Eval,
    pool: Sequence[Offer],
    allowance: float,
    min_gain: float = MIN_GAIN,
    holes_only: bool = False,
    capital_neutral: bool = False,
) -> tuple[frozenset[str], tuple[Offer, ...], _Eval] | None:
    """The buy (and the sales it names, if it needs any) that raises U the most.

    A buy qualifies if it fills an empty slot or adds `MIN_GAIN` expected points;
    it may name up to `SWAP_MAX_SALES` of T's players to keep K >= 0, must keep
    the wallet inside Kickbase's cap in transit, and must leave enough for the
    cheapest fills of the slots still empty."""
    holes_now = ELEVEN - len(now.eleven)
    if holes_only and holes_now == 0:
        return None
    # With no slot empty, a buy adds at most his EP minus the weakest starter's (a
    # formation change can move the weakest starter at any position out).
    weakest = min((p.ep_next for p in now.eleven), default=0.0)
    best: tuple[float, frozenset[str], tuple[Offer, ...], _Eval] | None = None
    for o in pool:
        if holes_now == 0 and o.player.ep_next < weakest + min_gain:
            continue
        trial = (*buys, o)
        if ctx.cash - sum(x.bid for x in trial) < -allowance:
            continue
        first = ctx.evaluate(removed, trial)
        options: list[tuple[frozenset[str], _Eval]] = [(removed, first)]
        if first.budget < 0:
            members = [p for p in first.eleven if p.player_id != o.player.player_id]
            options = [
                (gone, ctx.evaluate(gone, trial))
                for size in range(1, SWAP_MAX_SALES + 1)
                for group in combinations(members, size)
                for gone in (removed | _ids(group),)
            ]
        chosen = _ids(x.player for x in trial)
        for gone, e in options:
            holes = ELEVEN - len(e.eleven)
            if e.budget - _reserve(pool, chosen, holes) < 0:
                continue
            if holes >= holes_now and (holes_only or e.ep - now.ep < min_gain):
                continue
            if capital_neutral and holes >= holes_now:
                kept = _ids(e.eleven)
                leaving = sum(p.market_value for p in now.eleven if p.player_id not in kept)
                if o.bid > leaving + max(0, ctx.cash):
                    continue
            gain = e.utility - now.utility
            if gain > 0 and (best is None or gain > best[0]):
                best = (gain, gone, trial, e)
    return None if best is None else best[1:]
