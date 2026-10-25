"""Which way the price moved last night, read before a points buy.

The flip path has looked at the last nightly move since 2026-10-07; the points
buys never did, and they are most of what the bot buys. On 2026-10-25 51% of
our resold buys had risen the night before, against 78% for the league's top
three — whose profit this season is almost all the value gained while held.

Measured the same day on 1,484 league resales (2025-08 → 2026-10), with the
move a bidder could see (the update before the listing's own night):

- **Under 5m** a riser resold at a median +27.4% (79% won), a player who fell
  or stood still at -2.5% / 0.0% (43% / 29%), and both scored the same points
  after (30 vs 27). Not rising is refused outright.
- **5-15m**: the value while held moved about +10% for a riser and +0.5-1% for
  a faller, in both seasons. A faller is still bought when he is worth it, but
  the 9-point gap is charged against what winning him is worth
  (`value_bid.optimal_premium`'s ``resale_drift_eur``), so he gets a lower
  premium. Not a refusal: in this band risers scored 13 fewer points a match
  this season (n=50 vs 38) and none fewer the season before.
- **15m+**: resale lost whatever the direction; the premium cap is the rule.

An unknown move (no history) changes nothing — an unreadable signal must not
refuse a buy. Pure: callers pass the numbers in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntryDirectionRule:
    veto_below_mv: int = 5_000_000
    cost_below_mv: int = 15_000_000
    falling_drift_gap_pct: float = 9.0

    def veto_reason(self, market_value: int, last_move_pct: float | None) -> str | None:
        """Why a cheap player is not bought after a night he did not rise, or None."""
        if last_move_pct is None or market_value >= self.veto_below_mv:
            return None
        if last_move_pct > 0:
            return None
        return (
            f"did not rise last night ({last_move_pct:+.1f}%) — under "
            f"EUR {self.veto_below_mv:,} only risers resell at a profit"
        )

    def falling_cost_eur(self, market_value: int, last_move_pct: float | None) -> float:
        """What a mid-band player who did not rise is expected to give up at resale."""
        if last_move_pct is None or last_move_pct > 0:
            return 0.0
        if not self.veto_below_mv <= market_value < self.cost_below_mv:
            return 0.0
        return market_value * self.falling_drift_gap_pct / 100.0
