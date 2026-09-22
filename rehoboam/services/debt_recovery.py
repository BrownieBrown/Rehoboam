"""Who to sell so the wallet is back at zero before kickoff.

The bot may run a negative budget between matchdays — that is how it buys
good players it cannot yet afford — but a negative budget *at kickoff* is
zero points for the entire matchday. The last sessions before a round
therefore have to bring the wallet back to zero or better, and this module
decides at whose expense.

Marco's rule (2026-09-22): bank profits first, and do not lock in a transfer
loss on a starter who is merely in a slump. That is a weighing of several
things — profit against cost basis, points the eleven would lose, whether the
price is still falling — so the order below is explicit tiers rather than a
single formula:

1. bench players held at a profit, biggest profit share first;
2. starters held at a profit, fewest expected points first;
3. bench players held at a loss, smallest loss first, slumping prices last;
4. starters held at a loss, the same way.

A player whose position is at its formation minimum is never sold: that
would trade the debt for an empty slot. Pure, so every tier is testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import INSTANT_SELL_PCT, POSITION_MINIMUMS


@dataclass(frozen=True)
class DebtCandidate:
    """One squad player as the recovery sees him."""

    player_id: str
    name: str
    position: str
    market_value: int
    buy_price: int | None
    expected_points: float
    in_best_eleven: bool
    trend_7d_pct: float | None = None

    @property
    def profit_pct(self) -> float | None:
        """Market value over cost basis, in percent; None without a cost basis."""
        if not self.buy_price or self.buy_price <= 0:
            return None
        return (self.market_value - self.buy_price) / self.buy_price * 100.0

    @property
    def sell_value(self) -> int:
        return int(self.market_value * INSTANT_SELL_PCT)

    @property
    def at_a_loss(self) -> bool:
        pct = self.profit_pct
        return pct is not None and pct < 0

    @property
    def in_a_slump(self) -> bool:
        return self.at_a_loss and self.trend_7d_pct is not None and self.trend_7d_pct < 0


@dataclass(frozen=True)
class DebtPlan:
    sells: list[DebtCandidate]
    shortfall: int
    recovered: int

    @property
    def remaining(self) -> int:
        return max(0, self.shortfall - self.recovered)

    @property
    def covered(self) -> bool:
        return self.remaining == 0


def _sacrifice_key(c: DebtCandidate) -> tuple:
    """Sort key: lower sells first. See the module docstring for the tiers."""
    pct = c.profit_pct if c.profit_pct is not None else 0.0
    tier = (2 if c.at_a_loss else 0) + (1 if c.in_best_eleven else 0)
    if tier == 0:
        # Profitable bench: biggest profit share first; points are irrelevant.
        return (tier, -pct, 0.0, 0.0)
    if tier == 1:
        # Profitable starters: lose the fewest points, then bank the most.
        return (tier, c.expected_points, -pct, 0.0)
    # Losses: a slump goes last, then the shallowest loss, then fewest points.
    return (tier, 1.0 if c.in_a_slump else 0.0, -pct, c.expected_points)


def plan_debt_recovery(
    candidates: list[DebtCandidate],
    *,
    shortfall: int,
    position_counts: dict[str, int],
) -> DebtPlan:
    """Pick the sells that cover ``shortfall``, cheapest sacrifice first.

    ``shortfall`` is how far the wallet is below zero once open offers are
    counted (rule I3's definition). ``position_counts`` is the squad's head
    count per position; it is copied and decremented as sells are planned so
    the second sale at a position sees the first.
    """
    need = max(0, int(shortfall))
    sells: list[DebtCandidate] = []
    recovered = 0
    if need == 0:
        return DebtPlan(sells=sells, shortfall=need, recovered=0)

    counts = dict(position_counts)
    for cand in sorted(candidates, key=_sacrifice_key):
        if recovered >= need:
            break
        minimum = POSITION_MINIMUMS.get(cand.position, 0)
        if counts.get(cand.position, 0) <= minimum:
            continue
        sells.append(cand)
        recovered += cand.sell_value
        counts[cand.position] = counts.get(cand.position, 0) - 1
    return DebtPlan(sells=sells, shortfall=need, recovered=recovered)
