"""Sell more often when he is not a top player at his position (2026-09-24).

The profit-sell loop judges a held player by profit against cost and knows
nothing about quality. Marco's learning after the Castello Jr. buy was "sell
more often if not a top player, and don't overpay". The second half is the
win-curve and break-even work; this is the first half.

Two rules, pure so they can be tested exhaustively:

* **Out for weeks** (Kickbase status 1): sell regardless of rank. Status 2
  ("uncertain") is not this rule -- a good player who is doubtful this week is
  Marco's hold, not a sell.
* **Outside the top N at his position on BOTH measures**: points per
  appearance among players who have played (`player_ranks.avg_points_rank_pos`)
  and this week's expected points (`ep_rank_pos`). Either one inside the top
  N keeps him -- the proven player who is doubtful this week (Castello Jr.,
  avg 14th / EP 181st) and the player the model rates before the season has
  proven him (Burger, avg 44th / EP 7th, two days after a 24.5 m buy) are
  both holds; the one who is mediocre on both (Møller Wolfe, 100th / 43rd)
  is the sale. A held player needs `min_appearances` before his rank is
  trusted; fewer means unknown quality, never bad quality, so no sale on
  rank. A rebounding price (7d trend at or above +1%) defers the sale, the
  same guard the loss-sells use. A member of the best eleven is sold only
  with recovery time before kickoff, the same guard trade pairs use.

Neither rule reads the cost basis: a rank sell is a points decision, not a
profit decision, and it covers the player without a cost basis too.
"""

from __future__ import annotations

from dataclasses import dataclass

OUT_FOR_WEEKS = 1


@dataclass(frozen=True)
class RankSellInput:
    status: int | None
    avg_points_rank_pos: int | None
    ep_rank_pos: int | None
    appearances: int | None
    trend_7d_pct: float | None
    in_best_eleven: bool
    days_until_match: int | None


def rank_sell_reason(
    inp: RankSellInput,
    *,
    floor: int,
    min_appearances: int,
    min_days_for_starter: int,
) -> str | None:
    """The reason to sell, or None to hold."""
    if inp.status == OUT_FOR_WEEKS:
        return "Out for weeks (status 1)"

    if inp.avg_points_rank_pos is None or (inp.appearances or 0) < min_appearances:
        return None  # unknown quality is not bad quality
    if inp.ep_rank_pos is None:
        return None  # no prediction: the model cannot vouch either way
    if inp.avg_points_rank_pos <= floor or inp.ep_rank_pos <= floor:
        return None  # a top player on either measure
    if inp.trend_7d_pct is not None and inp.trend_7d_pct >= 1.0:
        return None  # rebounding: the same guard the loss-sells use
    if inp.in_best_eleven and not _has_recovery_time(inp.days_until_match, min_days_for_starter):
        return None
    return (
        f"Outside the top {floor} at position on both measures "
        f"(points per appearance {inp.avg_points_rank_pos}, expected points "
        f"{inp.ep_rank_pos}, {inp.appearances} apps)"
    )


def _has_recovery_time(days_until_match: int | None, min_days: int) -> bool:
    return days_until_match is not None and days_until_match >= min_days
