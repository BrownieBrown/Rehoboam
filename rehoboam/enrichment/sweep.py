"""League-wide corpus sweep — the long pole of week 1.

Enumerates every selectable player in the league and pulls per-match
performance plus the full market-value series into ``TrainingCorpus``. Around
a thousand requests, so it is throttled, resumable, and tolerant of individual
failures.

Resumability is the important property: ``sweep_progress`` is only marked
after a successful write, so an interrupted or partially-failed run picks up
exactly where it stopped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from rehoboam.enrichment.corpus import TrainingCorpus

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAME_DAYS = 365
DEFAULT_THROTTLE_SECONDS = 0.25
DEFAULT_PAGE_SIZE = 50
# Generous ceiling: the largest real position (MID) needed 4 pages. This only
# exists so a misbehaving endpoint cannot spin the loop forever.
DEFAULT_MAX_PAGES_PER_POSITION = 40

_POSITIONS = {1: "Goalkeeper", 2: "Defender", 3: "Midfielder", 4: "Forward"}


@dataclass
class SweepStats:
    universe_size: int = 0
    performance_fetched: int = 0
    mv_fetched: int = 0
    failed: int = 0
    skipped: int = 0


def _universe_to_rows(items: list[dict]) -> list[dict]:
    """Map lineup-selection items to ``upsert_players`` rows.

    Field names are measured from the live endpoint, not inherited from
    ``Player.from_dict`` — the two disagree. Here the id is ``pi``, and there
    is no first-name field at all, so ``first_name`` is always None.
    """
    rows = []
    for item in items:
        pid = item.get("pi")
        if pid is None:
            continue
        rows.append(
            {
                "player_id": str(pid),
                "first_name": None,
                "last_name": item.get("n"),
                "position": _POSITIONS.get(item.get("pos"), None),
                "team_id": item.get("tid"),
                "market_value": item.get("mv"),
                "average_points": item.get("ap"),
            }
        )
    return rows


def fetch_universe(
    client,
    league_id: str,
    *,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages_per_position: int = DEFAULT_MAX_PAGES_PER_POSITION,
) -> list[dict]:
    """Enumerate every selectable player by sweeping positions 1-4.

    ``position`` is mandatory on the endpoint — omitting it returns zero items
    rather than an error — so the universe is the union of four per-position
    sweeps, deduplicated by player id.

    Pagination note, load-bearing: the server caps each page at 50 items no
    matter what ``page_size`` requests. ``start`` therefore advances by the
    number of items actually returned. Advancing by ``page_size`` would skip
    every other page while still looking like a successful sweep.
    """
    by_id: dict[str, dict] = {}

    for position in (1, 2, 3, 4):
        start = 0
        for _ in range(max_pages_per_position):
            payload = client.get_lineup_selection(
                league_id=league_id,
                position=position,
                start=start,
                max_items=page_size,
            )
            items = payload.get("it") or []
            if not items:
                break

            for row in _universe_to_rows(items):
                by_id[row["player_id"]] = row

            start += len(items)  # actual count, never page_size
            if throttle_seconds:
                time.sleep(throttle_seconds)
        else:
            logger.warning(
                "Position %d hit the %d-page ceiling — universe may be truncated",
                position,
                max_pages_per_position,
            )

    logger.info("Universe: %d distinct players across 4 positions", len(by_id))
    return list(by_id.values())


def run_sweep(
    client,
    corpus: TrainingCorpus,
    *,
    league_id: str,
    dry_run: bool = False,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    limit: int | None = None,
    timeframe_days: int = DEFAULT_TIMEFRAME_DAYS,
) -> SweepStats:
    """Populate the training corpus for every player in the league.

    ``dry_run`` fetches the universe (so the size estimate is real) but
    performs no per-player fetches and no history writes.
    """
    stats = SweepStats()

    rows = fetch_universe(client, league_id, throttle_seconds=throttle_seconds)
    stats.universe_size = len(rows)
    corpus.upsert_players(rows)

    if dry_run:
        return stats

    team_by_id = {r["player_id"]: r.get("team_id") for r in rows}

    pending_perf = corpus.players_needing_fetch("performance")
    pending_mv = corpus.players_needing_fetch("mv")
    stats.skipped = stats.universe_size - len(pending_perf)

    if limit is not None:
        pending_perf = pending_perf[:limit]
        pending_mv = pending_mv[:limit]

    for pid in pending_perf:
        try:
            perf = client.get_competition_player_performance(player_id=pid)
            corpus.record_match_history(pid, team_by_id.get(pid), perf)
            corpus.mark_fetched(pid, performance=True)
            stats.performance_fetched += 1
        except Exception as e:
            stats.failed += 1
            logger.warning("Performance fetch failed for player %s: %s", pid, e)
        if throttle_seconds:
            time.sleep(throttle_seconds)

    for pid in pending_mv:
        try:
            history = client.get_player_market_value_history_v2(
                player_id=pid, timeframe=timeframe_days
            )
            corpus.record_mv_series(pid, history)
            corpus.mark_fetched(pid, mv=True)
            stats.mv_fetched += 1
        except Exception as e:
            stats.failed += 1
            logger.warning("MV fetch failed for player %s: %s", pid, e)
        if throttle_seconds:
            time.sleep(throttle_seconds)

    logger.info(
        "Sweep done: %d perf, %d mv, %d failed, %d skipped",
        stats.performance_fetched,
        stats.mv_fetched,
        stats.failed,
        stats.skipped,
    )
    return stats
