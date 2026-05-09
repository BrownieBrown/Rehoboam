"""REH-39: One-shot backfill of foundation tables from KICKBASE history.

Three phases against the live KICKBASE API:

1. ``/managers/{mid}/transfer`` paginated via ``?start=`` cursor → derive
   closed flips by FIFO buy/sell pairing per ``player_id`` → ``flip_outcomes``.
2. ``/users/{uid}/teamcenter?dayNumber=N`` → ``matchday_lineup_results``
   (with ``total_points`` summed from each player's ``p`` field).
3. ``/leagues/{lid}/ranking?dayNumber=N`` → ``league_rank_history``
   (one row per manager per matchday).

``team_value_history`` is intentionally NOT backfilled — its schema requires
``budget`` and ``squad_size`` which the ranking endpoint doesn't expose for
historical matchdays. The longitudinal team-value series is preserved via
``league_rank_history.team_value`` instead.

Idempotent: every writer call uses ``INSERT OR IGNORE`` against a meaningful
unique constraint (``flip_outcomes`` gets ``idx_flip_unique`` added in the
same PR; the other tables already had it).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .bid_learner import BidLearner, FlipOutcome
from .kickbase_client import KickbaseV4Client, League

logger = logging.getLogger(__name__)

TRANSFER_PAGE_SIZE = 25
TRANSFER_BUY = 1
TRANSFER_SELL = 2


@dataclass
class BackfillStats:
    transfers_paginated: int = 0
    flip_outcomes_inserted: int = 0
    flip_outcomes_skipped_duplicate: int = 0
    flip_outcomes_unpaired_buys: int = 0
    flip_outcomes_orphaned_sells: int = 0
    matchdays_processed: int = 0
    matchdays_skipped_no_lineup: int = 0
    league_rank_history_inserted: int = 0
    matchday_lineup_results_inserted: int = 0


@dataclass
class FlipPair:
    player_id: str
    player_name: str
    buy: dict[str, Any]
    sell: dict[str, Any]


def _to_epoch(iso_str: str) -> float:
    """Convert ISO-8601 with optional 'Z' suffix to a unix epoch float."""
    if iso_str.endswith("Z"):
        iso_str = iso_str[:-1] + "+00:00"
    return datetime.fromisoformat(iso_str).timestamp()


def _paginate_transfers(
    client: KickbaseV4Client, league_id: str, manager_id: str
) -> tuple[list[dict[str, Any]], int]:
    """Walk ``/managers/{mid}/transfer?start=N`` until the API stops returning
    a full page. Returns ``(all_items, page_count)``.
    """
    items: list[dict[str, Any]] = []
    pages = 0
    start = 0
    while True:
        page = client.get_manager_transfer_history(league_id, manager_id, start=start)
        pages += 1
        page_items = page.get("it") or []
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < TRANSFER_PAGE_SIZE:
            break
        start += TRANSFER_PAGE_SIZE
    return items, pages


def _pair_flips(transfers: list[dict[str, Any]]) -> tuple[list[FlipPair], int, int]:
    """FIFO buy→sell pairing per player_id.

    For each player_id, sort transfers ascending by ``dt`` and pair successive
    buys (``tty=1``) with the next sell (``tty=2``). Re-buys after a wash-trade
    window produce a second flip. Buys with no matching sell (still in squad)
    and sells with no preceding buy (data gap) are counted but not written.

    Returns ``(flips, unpaired_buy_count, orphaned_sell_count)``.
    """
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in transfers:
        by_player[t["pi"]].append(t)

    flips: list[FlipPair] = []
    unpaired = 0
    orphaned = 0

    for pi, items in by_player.items():
        items.sort(key=lambda x: x["dt"])
        buy_queue: list[dict[str, Any]] = []
        for t in items:
            tty = t.get("tty")
            if tty == TRANSFER_BUY:
                buy_queue.append(t)
            elif tty == TRANSFER_SELL:
                if buy_queue:
                    buy = buy_queue.pop(0)
                    flips.append(
                        FlipPair(
                            player_id=pi,
                            player_name=t.get("pn") or buy.get("pn") or "",
                            buy=buy,
                            sell=t,
                        )
                    )
                else:
                    orphaned += 1
                    logger.warning(
                        "Orphaned sell for player %s (%s) at %s — no preceding buy",
                        pi,
                        t.get("pn"),
                        t.get("dt"),
                    )
        unpaired += len(buy_queue)

    return flips, unpaired, orphaned


def _flip_to_outcome(pair: FlipPair) -> FlipOutcome:
    buy_epoch = _to_epoch(pair.buy["dt"])
    sell_epoch = _to_epoch(pair.sell["dt"])
    buy_price = int(pair.buy.get("trp", 0))
    sell_price = int(pair.sell.get("trp", 0))
    profit = sell_price - buy_price
    profit_pct = (profit / buy_price * 100.0) if buy_price else 0.0
    hold_days = max(0, int((sell_epoch - buy_epoch) // 86400))
    return FlipOutcome(
        player_id=pair.player_id,
        player_name=pair.player_name,
        buy_price=buy_price,
        sell_price=sell_price,
        profit=profit,
        profit_pct=profit_pct,
        hold_days=hold_days,
        buy_date=buy_epoch,
        sell_date=sell_epoch,
        trend_at_buy=None,
        average_points=None,
        position=None,
        was_injured=False,
    )


def _backfill_flip_outcomes(
    client: KickbaseV4Client,
    league_id: str,
    manager_id: str,
    learner: BidLearner,
    *,
    dry_run: bool,
    stats: BackfillStats,
) -> None:
    transfers, pages = _paginate_transfers(client, league_id, manager_id)
    stats.transfers_paginated = pages
    flips, unpaired, orphaned = _pair_flips(transfers)
    stats.flip_outcomes_unpaired_buys = unpaired
    stats.flip_outcomes_orphaned_sells = orphaned

    if dry_run:
        # Upper-bound estimate; we can't tell duplicates without writing.
        stats.flip_outcomes_inserted = len(flips)
        return

    for f in flips:
        if learner.record_flip(_flip_to_outcome(f)):
            stats.flip_outcomes_inserted += 1
        else:
            stats.flip_outcomes_skipped_duplicate += 1


def _ranking_row(
    user: dict[str, Any], league_id: str, day: int, snapshot_at: float, our_user_id: str
) -> dict[str, Any]:
    tv_raw = user.get("tv")
    return {
        "snapshot_at": snapshot_at,
        "league_id": league_id,
        "manager_id": str(user.get("i", "")),
        "day_number": day,
        "rank_overall": user.get("spl"),
        "rank_matchday": user.get("mdpl"),
        "total_points": user.get("sp"),
        "matchday_points": user.get("mdp"),
        "team_value": int(tv_raw) if tv_raw is not None else None,
        "is_self": 1 if str(user.get("i")) == our_user_id else 0,
    }


def _backfill_matchday_phase(
    client: KickbaseV4Client,
    league: League,
    user_id: str,
    learner: BidLearner,
    current_day: int,
    *,
    dry_run: bool,
    stats: BackfillStats,
) -> None:
    """For each matchday in [1..current_day]: fetch teamcenter + ranking, write rows."""
    for day in range(1, current_day + 1):
        tc = client.get_user_teamcenter(league.id, user_id, day_number=day)
        lp = tc.get("lp") or []
        if not lp:
            stats.matchdays_skipped_no_lineup += 1
            logger.info("Skipping matchday %d — no lineup recorded", day)
            continue

        matchday_date = lp[0].get("md", "")
        snapshot_at = _to_epoch(matchday_date) if matchday_date else 0.0
        total_points = sum(int(p.get("p", 0)) for p in lp)
        lineup_player_ids = [str(p.get("i", "")) for p in lp]

        if dry_run:
            stats.matchday_lineup_results_inserted += 1
        else:
            inserted = learner.record_matchday_lineup_result(
                league_id=league.id,
                day_number=day,
                matchday_date=matchday_date,
                total_points=total_points,
                lineup_player_ids=lineup_player_ids,
                lineup_count=len(lp),
                snapshot_at=snapshot_at,
            )
            if inserted:
                stats.matchday_lineup_results_inserted += 1

        ranking = client.get_league_ranking(league.id, day_number=day)
        users = ranking.get("us") or []
        rank_rows = [_ranking_row(u, league.id, day, snapshot_at, user_id) for u in users]

        if rank_rows:
            if dry_run:
                stats.league_rank_history_inserted += len(rank_rows)
            else:
                stats.league_rank_history_inserted += learner.record_league_rank_snapshot(rank_rows)

        stats.matchdays_processed += 1


def run_backfill(
    client: KickbaseV4Client,
    league: League,
    user_id: str,
    manager_id: str,
    learner: BidLearner,
    *,
    dry_run: bool = False,
) -> BackfillStats:
    """Backfill all three foundation phases. Idempotent.

    ``user_id`` and ``manager_id`` are the same KICKBASE id today (the
    ``/teamcenter`` endpoint addresses by user, ``/transfer`` by manager,
    but both resolve to ``client.user.id``). Kept as separate params so a
    future API split doesn't need a signature change.
    """
    stats = BackfillStats()

    current_ranking = client.get_league_ranking(league.id)
    # `day` is the current matchday number (matches what the live writer in
    # trader.py persists). `lfmd` is something else and was a misread during
    # scoping — confirmed against probe dumps where `day=33` matched the
    # bot's day_number=33 in league_rank_history.
    current_day = int(current_ranking.get("day") or 0)
    logger.info("Backfilling matchdays 1..%d (dry_run=%s)", current_day, dry_run)

    _backfill_flip_outcomes(client, league.id, manager_id, learner, dry_run=dry_run, stats=stats)
    logger.info(
        "Phase 1: %d flips, %d duplicates, %d unpaired buys, %d orphaned sells, %d transfer pages",
        stats.flip_outcomes_inserted,
        stats.flip_outcomes_skipped_duplicate,
        stats.flip_outcomes_unpaired_buys,
        stats.flip_outcomes_orphaned_sells,
        stats.transfers_paginated,
    )

    if current_day > 0:
        _backfill_matchday_phase(
            client, league, user_id, learner, current_day, dry_run=dry_run, stats=stats
        )
    logger.info(
        "Phase 2/3: %d matchdays processed (%d skipped), %d lineup rows, %d rank rows",
        stats.matchdays_processed,
        stats.matchdays_skipped_no_lineup,
        stats.matchday_lineup_results_inserted,
        stats.league_rank_history_inserted,
    )

    return stats
