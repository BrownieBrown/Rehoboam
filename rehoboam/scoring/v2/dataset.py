"""Corpus loading and the train/holdout split.

The split is the project's most important discipline: 2025/26 is the season the
whole rebuild is judged against, so nothing may be fitted on it. Seasons after
the holdout (2026/27 fixtures) are unplayed and belong in neither set.

Kickbase season titles are ``YYYY/YYYY`` and sort correctly under plain string
comparison, which is what the boundaries below rely on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rehoboam.scoring.v2.features import FeatureRow, MatchRow

TRAIN_MAX_SEASON = "2024/2025"
HOLDOUT_SEASON = "2025/2026"


def load_match_rows(db_path: Path) -> dict[str, list[MatchRow]]:
    """Load every played match from the corpus, grouped by player."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT player_id, season, day_number, status, points, minutes
            FROM player_match_history
            ORDER BY player_id, season, day_number
            """
        ).fetchall()

    by_player: dict[str, list[MatchRow]] = {}
    for player_id, season, day_number, status, points, minutes in rows:
        by_player.setdefault(str(player_id), []).append(
            MatchRow(
                player_id=str(player_id),
                season=season,
                day_number=int(day_number),
                status=status,
                points=int(points),
                minutes=int(minutes),
            )
        )
    return by_player


def load_positions(db_path: Path) -> dict[str, str]:
    """player_id → position, for players whose position is known."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT player_id, position FROM player_universe WHERE position IS NOT NULL"
        ).fetchall()
    return {str(pid): pos for pid, pos in rows}


def split_rows(rows: list[FeatureRow]) -> tuple[list[FeatureRow], list[FeatureRow]]:
    """Split into (train, holdout) by season.

    Train is everything up to and including ``TRAIN_MAX_SEASON``. Holdout is
    exactly ``HOLDOUT_SEASON``. Later seasons are dropped — they are fixtures,
    not results.
    """
    train = [r for r in rows if r.season <= TRAIN_MAX_SEASON]
    holdout = [r for r in rows if r.season == HOLDOUT_SEASON]
    return train, holdout
