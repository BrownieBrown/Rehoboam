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
    """The reason to sell, or None to hold.

    The quality verdict is `rank_floor_verdict`, shared with the buy side; the
    two guards after it only decide WHEN a held player goes.
    """
    verdict = rank_floor_verdict(
        status=inp.status,
        avg_points_rank_pos=inp.avg_points_rank_pos,
        ep_rank_pos=inp.ep_rank_pos,
        appearances=inp.appearances,
        floor=floor,
        min_appearances=min_appearances,
    )
    if verdict is None or inp.status == OUT_FOR_WEEKS:
        return verdict
    if inp.trend_7d_pct is not None and inp.trend_7d_pct >= 1.0:
        return None  # rebounding: the same guard the loss-sells use
    if inp.in_best_eleven and not _has_recovery_time(inp.days_until_match, min_days_for_starter):
        return None
    return verdict


def rank_floor_verdict(
    *,
    status: int | None,
    avg_points_rank_pos: int | None,
    ep_rank_pos: int | None,
    appearances: int | None,
    floor: int,
    min_appearances: int,
) -> str | None:
    """Why this player is not worth holding at his position, or None.

    One predicate for both sides (2026-10-25): the sell loop sells on it and
    the buy side refuses on it (`buy_rank_refusals`), so the bot can no longer
    buy a player the next session sells — Amoako, Seiwald, Džeko and Diks were
    bought as upgrades over a weak starter and rank-sold one to eleven days
    later, each round trip paying only the premium.
    """
    if status == OUT_FOR_WEEKS:
        return "Out for weeks (status 1)"
    if avg_points_rank_pos is None or (appearances or 0) < min_appearances:
        return None  # unknown quality is not bad quality
    if ep_rank_pos is None:
        return None  # no prediction: the model cannot vouch either way
    if avg_points_rank_pos <= floor or ep_rank_pos <= floor:
        return None  # a top player on either measure
    return (
        f"Outside the top {floor} at position on both measures "
        f"(points per appearance {avg_points_rank_pos}, expected points "
        f"{ep_rank_pos}, {appearances} apps)"
    )


def buy_rank_refusals(
    players: dict[str, object],
    ranks: dict[str, dict],
    *,
    floor: int,
    min_appearances: int,
) -> dict[str, str]:
    """player_id -> why the rank rule would sell him, for each candidate it would.

    ``players`` maps id to the market player (its ``status`` is read);
    ``ranks`` is `LeagueStore.position_ranks`. A player without a rank row is
    unknown quality and passes, unless Kickbase already has him out for weeks.
    """
    refused: dict[str, str] = {}
    for player_id, player in players.items():
        row = ranks.get(str(player_id)) or {}
        verdict = rank_floor_verdict(
            status=getattr(player, "status", None),
            avg_points_rank_pos=row.get("avg_points_rank_pos"),
            ep_rank_pos=row.get("ep_rank_pos"),
            appearances=row.get("appearances"),
            floor=floor,
            min_appearances=min_appearances,
        )
        if verdict is not None:
            refused[str(player_id)] = verdict
    return refused


def _has_recovery_time(days_until_match: int | None, min_days: int) -> bool:
    return days_until_match is not None and days_until_match >= min_days


def rank_hold_ids(ranks: dict[str, dict], *, floor: int, min_appearances: int) -> set[str]:
    """The players the rank rule keeps as top players at their position.

    Inside the floor on either measure — points per appearance or expected
    points — with at least ``min_appearances`` appearances. A sell plan may
    not fund a buy by selling one of these (2026-10-05: Castello Jr., 14th
    of all defenders by points per appearance and out "uncertain" that week,
    was the plan's first pick at -53% of cost). Unknown quality is not
    protected: protecting every new player would make every plan impossible.
    """
    held: set[str] = set()
    for player_id, row in (ranks or {}).items():
        if int(row.get("appearances") or 0) < int(min_appearances):
            continue
        avg_rank = row.get("avg_points_rank_pos")
        ep_rank = row.get("ep_rank_pos")
        if (avg_rank is not None and int(avg_rank) <= floor) or (
            ep_rank is not None and int(ep_rank) <= floor
        ):
            held.add(str(player_id))
    return held
