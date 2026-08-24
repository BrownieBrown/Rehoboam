"""Corpus loading and the train/holdout split.

2025/26 was held out while the v2 rebuild was being judged, and the model that
validated against it was then shipped as-is. That left the live scorer fitted
only on seasons up to 2024/25 — so every player whose Bundesliga record began
in 2025/26 had no fitted quality and fell back to a position prior. On
2026-08-24 that was 12 of 19 buyable listings sharing four identical EP values,
which is not a ranking.

Validation is done; the standard next step is to refit on everything before
serving. ``TRAIN_MAX_SEASON`` therefore now includes 2025/26.

The evidence for folding it in, measured on this corpus by fitting on <=S and
scoring S+1 with the live ``compose_ep``:

    train <= 2022/23 -> holdout 2023/24: Spearman 0.4373
    train <= 2023/24 -> holdout 2024/25: Spearman 0.5067
    train <= 2024/25 -> holdout 2025/26: Spearman 0.5170

Each added season improved out-of-sample ranking.

``HOLDOUT_SEASON`` is 2026/27, which is in progress: the corpus holds its
fixture list with ``status=0`` and no points, so ``build_feature_rows`` drops
it and the holdout is legitimately empty until results land. **While that is
true, no holdout evaluation is available** — and note that ``replay-season``
replays 2025/26, which is now inside training, so its number is optimistic and
is not a valid gate for scorer changes.

Kickbase season titles are ``YYYY/YYYY`` and sort correctly under plain string
comparison, which is what the boundaries below rely on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rehoboam.scoring.v2.features import FeatureRow, MatchRow

TRAIN_MAX_SEASON = "2025/2026"
HOLDOUT_SEASON = "2026/2027"


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
    exactly ``HOLDOUT_SEASON``. Later seasons are dropped.

    An empty holdout is expected mid-season: an in-progress season is stored as
    fixtures (``status=0``, no points), which ``build_feature_rows`` drops.
    """
    train = [r for r in rows if r.season <= TRAIN_MAX_SEASON]
    holdout = [r for r in rows if r.season == HOLDOUT_SEASON]
    return train, holdout
