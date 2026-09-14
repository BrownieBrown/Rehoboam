"""The store-backed corpus writer: what the ingestion and the sweep write.

Same rows as the SQLite ``TrainingCorpus`` (they share ``enrichment.rows``),
but in ``rehoboam.*``. The SQLite class stays the offline reader; this one
is where the league-wide data lands from now on (spec §2).
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from rehoboam.enrichment import rows as _rows

_PROGRESS_COLUMNS = {
    "performance": "performance_fetched_at",
    "mv": "mv_fetched_at",
    "transfers": "transfers_fetched_at",
    "status": "status_fetched_at",
}


class CorpusStore:
    """One transaction per call, on the store."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    # ---- writers -------------------------------------------------------

    def upsert_players(self, players: list[dict[str, Any]]) -> int:
        if not players:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rehoboam.player_universe (
                    player_id, first_name, last_name, position,
                    team_id, market_value, average_points
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    position = excluded.position,
                    team_id = excluded.team_id,
                    market_value = excluded.market_value,
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
        return len(players)

    def ensure_players(self, player_ids: list[str]) -> int:
        """Stub rows for ids new to the corpus; never overwrites (see TrainingCorpus)."""
        ids = [str(p) for p in player_ids]
        if not ids:
            return 0
        inserted = 0
        with self.connection() as conn:
            for pid in ids:
                cur = conn.execute(
                    "INSERT INTO rehoboam.player_universe (player_id) VALUES (%s) "
                    "ON CONFLICT (player_id) DO NOTHING",
                    (pid,),
                )
                inserted += cur.rowcount
        return inserted

    def record_match_history(
        self, player_id: str, team_id: str | None, performance: dict[str, Any]
    ) -> int:
        rows = _rows.match_history_rows(player_id, team_id, performance)
        if not rows:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rehoboam.player_match_history (
                    player_id, season, day_number, match_date, points,
                    minutes, team_id, opponent_team_id, is_home, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, season, day_number) DO UPDATE SET
                    match_date = excluded.match_date,
                    points = excluded.points,
                    minutes = excluded.minutes,
                    team_id = excluded.team_id,
                    opponent_team_id = excluded.opponent_team_id,
                    is_home = excluded.is_home,
                    status = excluded.status
                """,
                [
                    (
                        r["player_id"],
                        r["season"],
                        r["day_number"],
                        r["match_date"],
                        r["points"],
                        r["minutes"],
                        r["team_id"],
                        r["opponent_team_id"],
                        r["is_home"],
                        r["status"],
                    )
                    for r in rows
                ],
            )
        return len(rows)

    def record_mv_series(self, player_id: str, history: dict[str, Any]) -> int:
        rows = _rows.mv_series_rows(player_id, history)
        if not rows:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rehoboam.mv_series (player_id, snapshot_at, market_value) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                [(r["player_id"], r["snapshot_at"], r["market_value"]) for r in rows],
            )
        return len(rows)

    def record_player_transfers(self, player_id: str, history: dict[str, Any]) -> int:
        rows = _rows.transfer_rows(player_id, history)
        if not rows:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rehoboam.player_transfers (
                    player_id, transfer_at, price, transfer_type,
                    counterparty_id, counterparty_name
                ) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                """,
                [
                    (
                        r["player_id"],
                        r["transfer_at"],
                        r["price"],
                        r["transfer_type"],
                        r["counterparty_id"],
                        r["counterparty_name"],
                    )
                    for r in rows
                ],
            )
        return len(rows)

    def record_status_daily(
        self, player_id: str, day: date, details: dict[str, Any], fetched_at: float
    ) -> int:
        """One row per player per day; a second fetch the same day replaces it,
        so the row always carries the latest reading before kickoff."""
        r = _rows.status_row(player_id, day, details, fetched_at)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO rehoboam.player_status_daily (
                    player_id, day, status, lineup_probability, market_value, team_id, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, day) DO UPDATE SET
                    status = excluded.status,
                    lineup_probability = excluded.lineup_probability,
                    market_value = excluded.market_value,
                    team_id = excluded.team_id,
                    fetched_at = excluded.fetched_at
                """,
                (
                    r["player_id"],
                    r["day"],
                    r["status"],
                    r["lineup_probability"],
                    r["market_value"],
                    r["team_id"],
                    r["fetched_at"],
                ),
            )
        return 1

    def mark_fetched(
        self,
        player_id: str,
        *,
        performance: bool = False,
        mv: bool = False,
        transfers: bool = False,
        status: bool = False,
    ) -> None:
        """Record progress so an interrupted run resumes where it stopped."""
        now = time.time()
        wanted = [
            col
            for kind, col in _PROGRESS_COLUMNS.items()
            if {
                "performance": performance,
                "mv": mv,
                "transfers": transfers,
                "status": status,
            }[kind]
        ]
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO rehoboam.sweep_progress (player_id) VALUES (%s) "
                "ON CONFLICT (player_id) DO NOTHING",
                (str(player_id),),
            )
            for col in wanted:
                conn.execute(
                    f"UPDATE rehoboam.sweep_progress SET {col} = %s WHERE player_id = %s",
                    (now, str(player_id)),
                )

    def clear_performance_fetched(self, player_ids: list[str] | None = None) -> int:
        with self.connection() as conn:
            if player_ids is None:
                cur = conn.execute(
                    "UPDATE rehoboam.sweep_progress SET performance_fetched_at = NULL "
                    "WHERE performance_fetched_at IS NOT NULL"
                )
            else:
                ids = [str(p) for p in player_ids]
                if not ids:
                    return 0
                cur = conn.execute(
                    "UPDATE rehoboam.sweep_progress SET performance_fetched_at = NULL "
                    "WHERE performance_fetched_at IS NOT NULL AND player_id = ANY(%s)",
                    (ids,),
                )
            return cur.rowcount

    # ---- readers -------------------------------------------------------

    def players_needing_fetch(self, kind: str) -> list[str]:
        """Universe players never fetched for ``kind``, by id."""
        col = _PROGRESS_COLUMNS[kind]
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT u.player_id FROM rehoboam.player_universe u
                LEFT JOIN rehoboam.sweep_progress s ON s.player_id = u.player_id
                WHERE s.{col} IS NULL
                ORDER BY u.player_id
                """
            ).fetchall()
        return [r["player_id"] for r in rows]

    def players_needing_refresh(self, kind: str, *, older_than: float) -> list[str]:
        """Never fetched first, then stalest first — the order a budgeted run
        must process so an interrupted pass continues where it stopped."""
        col = _PROGRESS_COLUMNS[kind]
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT u.player_id FROM rehoboam.player_universe u
                LEFT JOIN rehoboam.sweep_progress s ON s.player_id = u.player_id
                WHERE s.{col} IS NULL OR s.{col} < %s
                ORDER BY s.{col} ASC NULLS FIRST, u.player_id
                """,
                (older_than,),
            ).fetchall()
        return [r["player_id"] for r in rows]

    def players_missing_position(self, player_ids: list[str]) -> list[str]:
        """Ids with no real position: stubs and ids unknown to the universe."""
        ids = [str(p) for p in player_ids]
        if not ids:
            return []
        with self.connection() as conn:
            known = conn.execute(
                "SELECT player_id FROM rehoboam.player_universe "
                "WHERE player_id = ANY(%s) AND position IS NOT NULL",
                (ids,),
            ).fetchall()
        resolved = {r["player_id"] for r in known}
        return sorted(i for i in ids if i not in resolved)

    def positions_for(self, player_ids: list[str]) -> dict[str, str]:
        ids = [str(p) for p in player_ids]
        if not ids:
            return {}
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, position FROM rehoboam.player_universe "
                "WHERE player_id = ANY(%s) AND position IS NOT NULL",
                (ids,),
            ).fetchall()
        return {r["player_id"]: r["position"] for r in rows}

    def status_on(self, player_id: str, day: date) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT player_id, day, status, lineup_probability, market_value, team_id, "
                "fetched_at FROM rehoboam.player_status_daily WHERE player_id = %s AND day = %s",
                (str(player_id), day),
            ).fetchone()
        return dict(row) if row else None
