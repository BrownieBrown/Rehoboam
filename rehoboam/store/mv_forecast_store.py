"""Readings in, forecasts and their outcomes out (spec 2026-09-17).

One transaction per call and bulk statements only: a run forecasts every
player it read, about 600, and must not pay one pooler round trip each.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from rehoboam.services.mv_forecast import Forecast, Score


class MvForecastStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def status_rows(self, day: date) -> list[dict[str, Any]]:
        """Every reading for `day` that carries a market value and its last change."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, market_value, mv_change, fetched_at "
                "FROM rehoboam.player_status_daily "
                "WHERE day = %s AND market_value IS NOT NULL AND mv_change IS NOT NULL "
                "ORDER BY player_id",
                (day,),
            ).fetchall()
        return [dict(r) for r in rows]

    def status_readings(self, day: date, player_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not player_ids:
            return {}
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, market_value, mv_change, fetched_at "
                "FROM rehoboam.player_status_daily WHERE day = %s AND player_id = ANY(%s)",
                (day, list(player_ids)),
            ).fetchall()
        return {r["player_id"]: dict(r) for r in rows}

    def upsert_forecasts(self, forecasts: list[Forecast], *, made_at: float, method: str) -> int:
        """Write forecasts; a second run the same day rewrites a row only while
        it is unscored."""
        if not forecasts:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rehoboam.mv_forecasts (
                    player_id, target_day, made_at, method, base_mv, last_change,
                    predicted_change, predicted_pct
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, target_day) DO UPDATE SET
                    made_at = excluded.made_at,
                    method = excluded.method,
                    base_mv = excluded.base_mv,
                    last_change = excluded.last_change,
                    predicted_change = excluded.predicted_change,
                    predicted_pct = excluded.predicted_pct
                WHERE rehoboam.mv_forecasts.scored_at IS NULL
                """,
                [
                    (
                        f.player_id,
                        f.target_day,
                        made_at,
                        method,
                        f.base_mv,
                        f.last_change,
                        f.predicted_change,
                        f.predicted_pct,
                    )
                    for f in forecasts
                ],
            )
            return cur.rowcount

    def forecasts_for(self, day: date) -> dict[str, float]:
        """player_id -> predicted change (fraction of base) for the update on `day`.

        Only unscored rows: a scored forecast describes an update that has
        already landed, and a bid must not discount it twice.
        """
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, predicted_pct FROM rehoboam.mv_forecasts "
                "WHERE target_day = %s AND scored_at IS NULL AND predicted_pct IS NOT NULL",
                (day,),
            ).fetchall()
        return {r["player_id"]: float(r["predicted_pct"]) for r in rows}

    def pending(self, before: date) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, target_day, base_mv FROM rehoboam.mv_forecasts "
                "WHERE scored_at IS NULL AND target_day < %s ORDER BY target_day, player_id",
                (before,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_outcomes(self, outcomes: list[tuple[str, date, Score]], *, scored_at: float) -> int:
        """Fill each forecast's outcome once; an already scored row is left alone.
        Returns how many rows were filled."""
        if not outcomes:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "UPDATE rehoboam.mv_forecasts SET scored_at = %s, outcome = %s, "
                "actual_change = %s, actual_pct = %s "
                "WHERE player_id = %s AND target_day = %s AND scored_at IS NULL",
                [
                    (
                        scored_at,
                        s.outcome,
                        s.actual_change,
                        s.actual_pct,
                        player_id,
                        target_day,
                    )
                    for player_id, target_day, s in outcomes
                ],
            )
            return cur.rowcount

    def daily_series(self) -> list[list[int]]:
        """Each player's stored daily market values, oldest first, split into
        runs of consecutive days."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, snapshot_at, market_value FROM rehoboam.mv_series "
                "ORDER BY player_id, snapshot_at"
            ).fetchall()
        runs: list[list[int]] = []
        last_player = None
        last_day = None
        for r in rows:
            day = int(r["snapshot_at"] // 86400)
            if r["player_id"] != last_player or last_day is None or day != last_day + 1:
                runs.append([])
            runs[-1].append(int(r["market_value"]))
            last_player, last_day = r["player_id"], day
        return runs
