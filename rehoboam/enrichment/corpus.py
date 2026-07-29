"""Durable training corpus for the v2 scorer.

Deliberately NOT ``value_history.performance_cache``: that table is a
6-hour TTL cache with a 7-day cleanup path (``value_history.py:171``).
Training data has to outlive both, so it gets its own database.

Schema is append-mostly and idempotent — the league-wide sweep in
``sweep.py`` is long-running, gets interrupted, and must be resumable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from rehoboam.match_parsing import parse_minutes

DEFAULT_CORPUS_PATH = Path("logs") / "training_corpus.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_universe (
    player_id      TEXT PRIMARY KEY,
    first_name     TEXT,
    last_name      TEXT,
    position       TEXT,
    team_id        TEXT,
    market_value   INTEGER,
    average_points REAL
);

CREATE TABLE IF NOT EXISTS player_match_history (
    player_id        TEXT NOT NULL,
    season           TEXT NOT NULL,
    day_number       INTEGER NOT NULL,
    match_date       TEXT,
    points           INTEGER NOT NULL,
    minutes          INTEGER NOT NULL,
    team_id          TEXT,
    opponent_team_id TEXT,
    is_home          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, season, day_number)
);

CREATE INDEX IF NOT EXISTS idx_match_history_player
    ON player_match_history(player_id, season, day_number);

CREATE TABLE IF NOT EXISTS mv_series (
    player_id    TEXT NOT NULL,
    snapshot_at  REAL NOT NULL,
    market_value INTEGER NOT NULL,
    PRIMARY KEY (player_id, snapshot_at)
);

CREATE TABLE IF NOT EXISTS sweep_progress (
    player_id             TEXT PRIMARY KEY,
    performance_fetched_at REAL,
    mv_fetched_at          REAL
);
"""


class TrainingCorpus:
    """Read/write access to the training corpus database."""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = DEFAULT_CORPUS_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def upsert_players(self, players: list[dict[str, Any]]) -> int:
        """Insert or update universe rows. Returns rows written."""
        if not players:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO player_universe (
                    player_id, first_name, last_name, position,
                    team_id, market_value, average_points
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    first_name     = excluded.first_name,
                    last_name      = excluded.last_name,
                    position       = excluded.position,
                    team_id        = excluded.team_id,
                    market_value   = excluded.market_value,
                    average_points = excluded.average_points
                """,
                [
                    (
                        str(p["player_id"]),
                        p.get("first_name"),
                        p.get("last_name"),
                        p.get("position"),
                        str(p["team_id"]) if p.get("team_id") is not None else None,
                        p.get("market_value"),
                        p.get("average_points"),
                    )
                    for p in players
                ],
            )
            conn.commit()
        return len(players)

    def ensure_players(self, player_ids: list[str]) -> int:
        """Create a bare ``player_universe`` stub for ids not already present.

        Unlike ``upsert_players``, this never overwrites an existing row —
        it only inserts a row (``player_id`` set, every other column left
        NULL) for an id that is entirely new to the corpus. Used by the
        historical-ids sweep extension (``sweep.run_sweep``'s
        ``extra_player_ids``): departed players recovered from the learning
        DB carry no position/name from any source we have, so a stub is the
        most we can safely claim, and a rerun must not clobber a row that
        later gained real data through some other path.

        Returns the number of rows actually inserted.
        """
        ids = {str(pid) for pid in player_ids}
        if not ids:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in ids)
            existing = {
                row[0]
                for row in conn.execute(
                    f"SELECT player_id FROM player_universe WHERE player_id IN ({placeholders})",
                    list(ids),
                )
            }
            new_ids = sorted(ids - existing)
            if new_ids:
                conn.executemany(
                    "INSERT INTO player_universe (player_id) VALUES (?)",
                    [(pid,) for pid in new_ids],
                )
                conn.commit()
        return len(new_ids)

    def record_match_history(
        self, player_id: str, team_id: str | None, performance: dict[str, Any]
    ) -> int:
        """Flatten a performance response into per-match rows.

        Shape: ``{"it": [{"ti": "2025/2026", "ph": [{...match...}]}]}``.
        Matches without a ``day`` are skipped — they cannot be placed on a
        timeline and so are useless for both training and backtesting.
        """
        rows: list[tuple] = []
        team = str(team_id) if team_id is not None else None

        for season in performance.get("it") or []:
            title = season.get("ti")
            if not title:
                continue
            for m in season.get("ph") or []:
                day = m.get("day")
                if day is None:
                    continue
                t1 = str(m.get("t1", "")) or None
                t2 = str(m.get("t2", "")) or None
                is_home = 1 if team is not None and team == t1 else 0
                opponent = t2 if is_home else t1
                rows.append(
                    (
                        str(player_id),
                        str(title),
                        int(day),
                        m.get("md"),
                        int(m.get("p") or 0),
                        parse_minutes(m.get("mp")),
                        team,
                        opponent,
                        is_home,
                    )
                )

        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO player_match_history (
                    player_id, season, day_number, match_date, points,
                    minutes, team_id, opponent_team_id, is_home
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        return len(rows)

    def record_mv_series(self, player_id: str, history: dict[str, Any]) -> int:
        """Persist a market-value series.

        Shape: ``{"it": [{"dt": <days_since_epoch>, "mv": <value>}]}``.
        Non-positive ``mv`` is a sentinel for newly-listed players and is
        dropped — same rule as ``mv_backfill._history_to_rows``.
        """
        rows = [
            (str(player_id), float(item["dt"]) * 86400.0, int(item["mv"]))
            for item in (history.get("it") or [])
            if item.get("dt") is not None and item.get("mv") and item["mv"] > 0
        ]
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO mv_series (player_id, snapshot_at, market_value) "
                "VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)

    def mark_fetched(self, player_id: str, *, performance: bool = False, mv: bool = False) -> None:
        """Record sweep progress so an interrupted run resumes cleanly."""
        import time

        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sweep_progress (player_id) VALUES (?)",
                (str(player_id),),
            )
            if performance:
                conn.execute(
                    "UPDATE sweep_progress SET performance_fetched_at = ? WHERE player_id = ?",
                    (now, str(player_id)),
                )
            if mv:
                conn.execute(
                    "UPDATE sweep_progress SET mv_fetched_at = ? WHERE player_id = ?",
                    (now, str(player_id)),
                )
            conn.commit()

    def players_needing_fetch(self, kind: str) -> list[str]:
        """Universe players with no successful fetch of ``kind`` yet.

        ``kind`` is ``"performance"`` or ``"mv"``.
        """
        column = {"performance": "performance_fetched_at", "mv": "mv_fetched_at"}[kind]
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT u.player_id
                FROM player_universe u
                LEFT JOIN sweep_progress s ON s.player_id = u.player_id
                WHERE s.{column} IS NULL
                ORDER BY u.player_id
                """
            ).fetchall()
        return [r[0] for r in rows]

    def matches_for_player(self, player_id: str) -> list[dict[str, Any]]:
        """All recorded matches for a player, oldest first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT season, day_number, match_date, points, minutes,
                       team_id, opponent_team_id, is_home
                FROM player_match_history
                WHERE player_id = ?
                ORDER BY season, day_number
                """,
                (str(player_id),),
            ).fetchall()
        return [dict(r) for r in rows]
