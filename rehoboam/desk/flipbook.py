"""The flip book: the leaders' trade in three rules (spec v3 §12, M2-17).

The planner (ADR-0003) re-solves a 7-day objective every 12 hours, so the policy
it executes is a chain of one- or two-day holds, each paying a premium priced
for seven days (M2-14). The league's leaders do the opposite: they buy a riser,
hold him through the run, sell at the turn, and fund it on debt they repay
before the kickoff (*Eval* §7.3). In the same simulator that trade made +€48m
over W1 against the planner's +€14m.

1. **Exit at the turn.** A flip is sold the morning after the first night his
   value did not rise (measured 2026-10-07: the turn kept 82% of a run's peak).
2. **Enter on the forecast.** Bid on the listings with the highest expected
   drift that clears its premium, while squad slots and the debt allowance
   allow. A bid that settles too late to be sold before the kickoff is bought
   only with cash.
3. **Repay before the kickoff.** The last session sells whatever costs the best
   eleven the fewest expected points per euro (the bench first, flips before
   the core), until the wallet is at zero. A negative wallet at kickoff scores
   nothing; an empty slot scores -100.

A flip is a player we won at auction and still hold: a fact from our own
transfer history, not a label (ADR-0002). Pure, like the rest of `desk`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations

from rehoboam.desk.eleven import best_eleven
from rehoboam.desk.model import Offer, Player

REPAY_MAX_SET = 3  # sales a repayment searches over together, as the legacy recovery did
REPAY_POOL = 12  # the candidates by points per euro the search draws from


def exits(flips: Sequence[Player], last_moves: Mapping[str, float | None]) -> tuple[Player, ...]:
    """The flips whose last nightly move was not a rise. An unknown move holds."""
    out = []
    for p in flips:
        move = last_moves.get(p.player_id)
        if move is not None and move <= 0:
            out.append(p)
    return tuple(out)


def repay(
    flips: Sequence[Player],
    core: Sequence[Player],
    *,
    cash: int,
    last_moves: Mapping[str, float | None],
) -> tuple[Player, ...]:
    """Sales that bring ``cash`` (net of open bids and earlier sales) to zero.

    The smallest sufficient sacrifice (the legacy recovery's lesson of
    2026-10-07): among sets of up to `REPAY_MAX_SET` sales that cover what is
    owed, the one that costs the best eleven the fewest expected points (an
    empty slot counting `EMPTY_SLOT_POINTS`), then the fewest sales, then the
    least cash to spare. Points lost are not additive (formations), so each set
    is scored on its own eleven. If no such set covers it, sell greedily by the
    fewest points per euro, as `planner._repair` does. The fixed order this
    replaced sold a third defender before a spare midfielder: 24 of 75 kickoffs
    were short (M2-23).
    """
    if cash >= 0:
        return ()
    flip_ids = {p.player_id for p in flips}
    squad = [*flips, *core]
    base, _ = best_eleven(squad)

    def lost(sold: Sequence[Player]) -> float:
        ids = {p.player_id for p in sold}
        return base - best_eleven([q for q in squad if q.player_id not in ids])[0]

    def order(p: Player) -> tuple[bool, float]:  # flips first, the worst last move first
        return (p.player_id not in flip_ids, last_moves.get(p.player_id) or 0.0)

    by_rate = sorted(squad, key=lambda p: (lost([p]) / max(1, p.market_value), *order(p)))
    pool = by_rate[:REPAY_POOL]
    best: tuple[tuple[float, int, int], tuple[Player, ...]] | None = None
    for size in range(1, REPAY_MAX_SET + 1):
        for group in combinations(pool, size):
            raised = sum(p.market_value for p in group)
            if cash + raised < 0:
                continue
            key = (lost(group), size, cash + raised)
            if best is None or key < best[0]:
                best = (key, group)
    if best is not None:
        return tuple(sorted(best[1], key=order))
    sales: list[Player] = []
    for p in by_rate:  # nothing small covers it: sell down the list
        if cash >= 0:
            break
        sales.append(p)
        cash += p.market_value
    return tuple(sales)


def entries(
    offers: Sequence[Offer],
    cash: int,
    slots: int,
    allowance: float,
    *,
    by_euros: bool = False,
    cover: float | None = None,
) -> tuple[Offer, ...]:
    """Bids by expected drift, highest first.

    ``cash`` is the wallet net of open bids and this session's sales; the bids
    may take it down to ``-allowance``, except a bid that cannot be sold before
    the kickoff, which needs the cash. ``by_euros`` ranks by the expected gain
    after the premium in euros, ``(drift − premium) × value``: the return per
    slot, for when slots bind rather than money (M2-18). ``cover`` is what the
    flips already held (and their open bids) raise at their p10; with it, the
    debt may not pass that plus the p10 of the new bids, so the flips alone
    can repay it before the kickoff (M2-23).
    """

    def rank(o: Offer) -> float:
        edge = o.player.drift - o.premium
        return edge * o.player.market_value if by_euros else o.player.drift

    bids: list[Offer] = []
    for o in sorted(offers, key=rank, reverse=True):
        if len(bids) >= slots:
            break
        if o.player.drift < o.premium:
            continue
        limit = allowance
        if cover is not None:
            limit = min(allowance, cover + _p10(o))
        floor = -limit if o.sellable_before_kickoff else 0
        if cash - o.bid < floor:
            continue
        bids.append(o)
        cash -= o.bid
        if cover is not None and o.sellable_before_kickoff:
            cover += _p10(o)
    return tuple(bids)


def _p10(o: Offer) -> float:
    """What a flip raises if sold before the kickoff, at the p10 of his drift."""
    return o.player.market_value * (1 + min(0.0, o.player.drift_low))
