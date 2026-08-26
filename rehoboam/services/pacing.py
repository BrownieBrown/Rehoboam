"""How much budget a buy must leave behind (REH-85).

`bidding_strategy.max_bid_fraction` asks what fraction of the *current* budget
a signing justifies — a single-decision question with a defensible answer.
Nothing asked the sequential one: what does this signing leave the bot able to
do for the next thirty matchdays? A competition-modelled replay answered it —
EUR 71m on one player, then five buys all season and EUR 500,000 left, with
every declined candidate rated `must_have` by the bot's own tiering.

Measured against `manager_transfers`, the champions each made ONE signing of
EUR 60-65m *and* roughly 25 further purchases. So the rule here constrains what
a buy leaves behind, never how large it is. A hard per-transaction cap near
their EUR 11m mean — the ticket's first suggestion — would have banned both of
their biggest signings; that mean is an artefact of a heavily skewed
distribution.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Kickbase's hard squad-size limit, including open (unresolved) bids.
SQUAD_CAP = 15


def available_squad_slots(squad_size: int, open_bid_count: int, cap: int = SQUAD_CAP) -> int:
    """Slots left under the squad cap, counting open bids as committed.

    Kickbase counts pending offers toward the 15-player cap before they
    resolve — a squad at 13 with 2 open bids has zero room for a further bid,
    not two. May return a negative number when over-committed; callers that
    need a floor apply their own.
    """
    return cap - squad_size - open_bid_count


def median_move_price(prices: Sequence[int], *, floor_eur: int) -> int:
    """What one further purchase costs in this league, in euros.

    The median rather than the mean, because the population is heavily skewed:
    the champions' buys have a median near EUR 10m and a maximum of EUR 65m, and
    a mean would price a "move" at something nobody routinely pays.

    On an even count this takes the lower of the two middle values rather than
    averaging them, so the result is always a price someone actually paid.

    `floor_eur` guards the thin-window case. A near-empty window would otherwise
    produce a reserve of nearly zero, which disables pacing silently — exactly
    when there is least evidence to justify spending freely.
    """
    if not prices:
        return floor_eur
    ordered = sorted(int(p) for p in prices)
    median = ordered[(len(ordered) - 1) // 2]
    return max(median, floor_eur)


def capital_reserve(*, slots_to_fill: int, in_season_min_moves: int, median_move: int) -> int:
    """Euros that must survive this buy, so the bot can keep operating.

    `slots_to_fill` rather than a constant is the load-bearing choice. A
    constant N keeps the reserve fixed while the budget falls, so the bot
    freezes one purchase later than it does today rather than not at all.
    Deriving it from unfilled slots makes the reserve unwind as the squad
    completes, and `in_season_min_moves` is what remains at 15/15 so a full
    squad can still replace a player.
    """
    moves = slots_to_fill if slots_to_fill > 0 else in_season_min_moves
    return max(0, moves) * max(0, median_move)


@dataclass(frozen=True)
class PacingContext:
    """What pacing needs that the bidder itself cannot know.

    Built once per session by the caller, which is the only layer that sees the
    squad, the open offers and the learning DB. `SmartBidding` stays ignorant of
    all three and just applies the number.
    """

    reserve: int
    open_offers: int

    def max_bid(self, budget_ceiling: int) -> int:
        """The largest bid that still leaves the reserve intact.

        `budget_ceiling` already includes any sell-plan recovery, which is why
        trade pairs need no special case: `trader.py` gives a pair a synthetic
        sell plan whose `total_recovery` is the sale proceeds, so subtracting
        the reserve from the ceiling paces the pair on its NET cost. A pair
        recycles capital rather than consuming it, and pacing it on the gross
        bid would freeze pair trading at 15/15 — the one mechanism a full squad
        has to improve itself.
        """
        return max(0, int(budget_ceiling) - int(self.open_offers) - int(self.reserve))
