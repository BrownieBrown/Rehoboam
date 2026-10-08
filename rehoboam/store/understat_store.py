"""`rehoboam.understat_player_stats` (migration 029): one snapshot per day fetched."""

from __future__ import annotations

from typing import Any

_COLUMNS = (
    "season",
    "understat_id",
    "day",
    "player_name",
    "team_title",
    "player_id",
    "position",
    "games",
    "time_played",
    "goals",
    "assists",
    "shots",
    "key_passes",
    "npg",
    "yellow_cards",
    "red_cards",
    "xg",
    "xa",
    "npxg",
    "xg_chain",
    "xg_buildup",
    "fetched_at",
)


class UnderstatStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def universe(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, first_name, last_name, team_id FROM rehoboam.player_universe"
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_fetched_at(self, season: str) -> float | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(fetched_at) AS at FROM rehoboam.understat_player_stats WHERE season = %s",
                (season,),
            ).fetchone()
        return float(row["at"]) if row and row["at"] is not None else None

    def write(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        cols = ", ".join(_COLUMNS)
        placeholders = ", ".join(["%s"] * len(_COLUMNS))
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in _COLUMNS if c not in ("season", "understat_id", "day")
        )
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO rehoboam.understat_player_stats ({cols}) VALUES ({placeholders}) "  # nosec B608
                f"ON CONFLICT (season, understat_id, day) DO UPDATE SET {updates}",
                [[r.get(c) for c in _COLUMNS] for r in rows],
            )
        return len(rows)

    def latest(self, season: str) -> list[dict[str, Any]]:
        """The newest snapshot per player."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (understat_id) * FROM rehoboam.understat_player_stats "
                "WHERE season = %s ORDER BY understat_id, day DESC",
                (season,),
            ).fetchall()
        return [dict(r) for r in rows]
