"""Reconstruct squad membership at a point in time.

``matchday_lineup_results`` records only the fielded 11, but lineup regret
needs the full squad — measured against a squad of exactly the fielded
players, regret is always zero and the metric says nothing.

Membership is derived from two sources, unioned:
  1. ``flip_outcomes`` hold windows (buy_date .. sell_date)
  2. the players actually fielded that matchday

Source 2 matters because a player bought and never sold has no flip row at
all. This is an approximation — a player held but benched all season, and
never flipped, is invisible. The harness labels the resulting regret figure
as medium fidelity.
"""

from __future__ import annotations

from typing import Any


def squad_on_matchday(
    flips: list[dict[str, Any]], fielded_ids: list[str], matchday_ts: float
) -> set[str]:
    """Player IDs plausibly in the squad at ``matchday_ts``.

    Args:
        flips: rows with ``player_id``, ``buy_date``, ``sell_date`` (unix seconds)
        fielded_ids: player IDs actually fielded that matchday
        matchday_ts: unix timestamp of the matchday

    Returns:
        Set of player IDs. Hold windows are inclusive at both ends.
    """
    squad = {str(pid) for pid in fielded_ids}
    for flip in flips:
        if flip["buy_date"] <= matchday_ts <= flip["sell_date"]:
            squad.add(str(flip["player_id"]))
    return squad
