"""REH-40: One-shot backfill of player_mv_history from KICKBASE history.

Why this exists: ``player_mv_history`` (REH-26) only captures snapshots for
players currently in the squad. Anything we already flipped left the squad
before the snapshot was taken, so the table has zero coverage for the 143
historical flips backfilled by REH-39. This blocks REH-32 / REH-33 which
need worst-dip / peak-MV computations across each flip's hold period.

This module fixes that by walking every distinct ``player_id`` in
``flip_outcomes`` and fetching the full season's MV history via the v2
endpoint (``/v4/competitions/1/players/{pid}/marketValue/{timeframe}``).
Each daily point becomes a row in ``player_mv_history``; the existing
``INSERT OR IGNORE`` PK keeps reruns idempotent.

Forward-looking coverage of market players is handled inline in
``trader.py`` (it's free — the market data is already in memory each
session). This module is one-shot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .bid_learner import BidLearner
from .kickbase_client import KickbaseV4Client

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAME_DAYS = 365  # full season


@dataclass
class MvBackfillStats:
    players_processed: int = 0
    players_skipped_no_data: int = 0
    players_failed: int = 0
    rows_attempted: int = 0  # upper bound — actual inserts dedupe via INSERT OR IGNORE


def _distinct_flip_player_ids(learner: BidLearner) -> list[str]:
    import sqlite3

    with sqlite3.connect(learner.db_path) as conn:
        rows = conn.execute("SELECT DISTINCT player_id FROM flip_outcomes").fetchall()
    return [r[0] for r in rows]


def _history_to_rows(player_id: str, history: dict[str, Any]) -> list[dict[str, Any]]:
    """Transform v2 history response into ``record_player_mv_snapshot`` rows.

    The v2 response shape is ``{"it": [{"dt": <days_since_epoch>, "mv": <value>}, ...]}``.
    ``dt * 86400`` gives a unix epoch in seconds. Empty / non-positive ``mv``
    is dropped (newly-listed players get sentinel rows).

    ``peak_mv_30d`` / ``trough_mv_30d`` are intentionally NULL on backfilled
    rows — the live writer's 30-day window only makes sense at write time;
    backfilled trajectories are point-in-time snapshots, not derived metrics.
    """
    items = history.get("it") or []
    rows: list[dict[str, Any]] = []
    for item in items:
        dt = item.get("dt")
        mv = item.get("mv")
        if dt is None or not mv or mv <= 0:
            continue
        rows.append(
            {
                "player_id": player_id,
                "snapshot_at": float(dt) * 86400.0,
                "market_value": int(mv),
                "peak_mv_30d": None,
                "trough_mv_30d": None,
            }
        )
    return rows


def run_mv_backfill(
    client: KickbaseV4Client,
    learner: BidLearner,
    *,
    dry_run: bool = False,
    timeframe_days: int = DEFAULT_TIMEFRAME_DAYS,
) -> MvBackfillStats:
    """Fetch + persist MV trajectories for every player in flip_outcomes.

    Idempotent: ``record_player_mv_snapshot`` is INSERT OR IGNORE on
    ``(player_id, snapshot_at)``, so reruns silently dedupe.

    ``dry_run`` performs all HTTP calls so the count estimate reflects
    real coverage, but skips DB writes.
    """
    stats = MvBackfillStats()
    player_ids = _distinct_flip_player_ids(learner)
    logger.info(
        "Backfilling MV history for %d distinct players (dry_run=%s, timeframe=%dd)",
        len(player_ids),
        dry_run,
        timeframe_days,
    )

    for pid in player_ids:
        try:
            history = client.get_player_market_value_history_v2(
                player_id=pid, timeframe=timeframe_days
            )
        except Exception as e:
            stats.players_failed += 1
            logger.warning("Failed to fetch MV history for player %s: %s", pid, e)
            continue

        rows = _history_to_rows(pid, history)
        if not rows:
            stats.players_skipped_no_data += 1
            continue

        stats.rows_attempted += len(rows)
        stats.players_processed += 1

        if not dry_run:
            learner.record_player_mv_snapshot(rows)

    logger.info(
        "MV backfill done: %d players, %d rows attempted, %d failed, %d empty",
        stats.players_processed,
        stats.rows_attempted,
        stats.players_failed,
        stats.players_skipped_no_data,
    )
    return stats
