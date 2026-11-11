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
3. **Repay before the kickoff.** The last session sells flips first, the worst
   last move first, then the squad's lowest expected points per euro, until
   the wallet is at zero. A negative wallet at kickoff scores nothing.

A flip is a player we won at auction and still hold: a fact from our own
transfer history, not a label (ADR-0002). Pure, like the rest of `desk`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from rehoboam.desk.model import Offer, Player


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
    """Sales that bring ``cash`` (net of open bids and earlier sales) to zero."""
    order = sorted(flips, key=lambda p: last_moves.get(p.player_id) or 0.0)
    order += sorted(core, key=lambda p: p.ep_next / max(1, p.market_value))
    sales: list[Player] = []
    for p in order:
        if cash >= 0:
            break
        sales.append(p)
        cash += p.market_value
    return tuple(sales)


def entries(
    offers: Sequence[Offer], cash: int, slots: int, allowance: float, *, by_euros: bool = False
) -> tuple[Offer, ...]:
    """Bids by expected drift, highest first.

    ``cash`` is the wallet net of open bids and this session's sales; the bids
    may take it down to ``-allowance``, except a bid that cannot be sold before
    the kickoff, which needs the cash. ``by_euros`` ranks by the expected gain
    after the premium in euros, ``(drift − premium) × value``: the return per
    slot, for when slots bind rather than money (M2-18).
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
        floor = -allowance if o.sellable_before_kickoff else 0
        if cash - o.bid < floor:
            continue
        bids.append(o)
        cash -= o.bid
    return tuple(bids)
