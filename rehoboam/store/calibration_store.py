"""Predictions, calibration rows and reports in the store (PR E).

One transaction per call, bulk queries only: the trading session scores
~460 players and must not pay one pooler round trip per player (PR C1
measured ~1 s each before it pinned a connection).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from rehoboam.scoring.store_scorer import StoredPlayer

_PREDICTION_COLUMNS = (
    "session_id",
    "player_id",
    "season",
    "day_number",
    "kickoff",
    "predicted_at",
    "predicted_ep",
    "p_status",
    "rate",
    "prev_status",
    "live_status",
    "position",
    "team_id",
    "owned",
    "listed",
    "in_best_11",
    "live_ep",
    "data_grade",
    "app",
    "dry_run",
    "backfill",
)


class CalibrationStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def current_season(self) -> str | None:
        """The newest season title in the corpus, e.g. `2026/2027`."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(season) AS season FROM rehoboam.player_match_history"
            ).fetchone()
        return row["season"] if row and row["season"] else None

    def stored_players(
        self, *, since_iso: str, status_day: date, before_iso: str | None = None
    ) -> list[StoredPlayer]:
        """Every player with a position, with his newest status row on or after
        `status_day - 1` and his match rows dated in `[since_iso, before_iso)`.

        `before_iso` is the leak boundary for a backfill: rows dated at or after
        it never reach the scorer. Match dates are ISO strings in the store
        (`2026-09-19T13:30:00Z`), so the comparison is textual and only valid
        for that exact format — which is what `rows.match_history_rows` writes.
        """
        with self.connection() as conn:
            universe = conn.execute(
                "SELECT player_id, position, team_id, market_value FROM rehoboam.player_universe "
                "WHERE position IS NOT NULL ORDER BY player_id"
            ).fetchall()
            status = conn.execute(
                "SELECT DISTINCT ON (player_id) player_id, status, lineup_probability, fetched_at "
                "FROM rehoboam.player_status_daily WHERE day >= %s "
                "ORDER BY player_id, day DESC",
                (status_day - timedelta(days=1),),
            ).fetchall()
            if before_iso is not None:
                match_query = (
                    "SELECT player_id, season, day_number, match_date, points, minutes, status "
                    "FROM rehoboam.player_match_history "
                    "WHERE match_date >= %s AND match_date < %s "
                    "ORDER BY player_id, season, day_number"
                )
                match_params: list[Any] = [since_iso, before_iso]
            else:
                match_query = (
                    "SELECT player_id, season, day_number, match_date, points, minutes, status "
                    "FROM rehoboam.player_match_history WHERE match_date >= %s "
                    "ORDER BY player_id, season, day_number"
                )
                match_params = [since_iso]
            matches = conn.execute(match_query, match_params).fetchall()
        status_by_id = {r["player_id"]: r for r in status}
        matches_by_id: dict[str, list[dict[str, Any]]] = {}
        for m in matches:
            matches_by_id.setdefault(m["player_id"], []).append(dict(m))
        out: list[StoredPlayer] = []
        for u in universe:
            s = status_by_id.get(u["player_id"])
            out.append(
                StoredPlayer(
                    player_id=u["player_id"],
                    position=u["position"],
                    team_id=u["team_id"],
                    market_value=u["market_value"],
                    live_status=s["status"] if s else None,
                    lineup_probability=s["lineup_probability"] if s else None,
                    status_fetched_at=float(s["fetched_at"]) if s else None,
                    matches=matches_by_id.get(u["player_id"], []),
                )
            )
        return out

    def write_predictions(self, rows: list[dict[str, Any]]) -> int:
        """Upsert on `(session_id, player_id)`; `p_status` keys become strings in jsonb."""
        if not rows:
            return 0
        cols = ", ".join(_PREDICTION_COLUMNS)
        placeholders = ", ".join(["%s"] * len(_PREDICTION_COLUMNS))
        updates = ", ".join(
            f"{c} = excluded.{c}"
            for c in _PREDICTION_COLUMNS
            if c not in ("session_id", "player_id")
        )
        values = []
        for r in rows:
            row = dict(r)
            row["p_status"] = Jsonb({str(k): v for k, v in row["p_status"].items()})
            values.append([row[c] for c in _PREDICTION_COLUMNS])
        with self.connection() as conn, conn.cursor() as cur:
            # nosec B608 -- identifiers come from the `_PREDICTION_COLUMNS` constant;
            # every value is a `%s` parameter.
            cur.executemany(
                f"INSERT INTO rehoboam.predictions ({cols}) VALUES ({placeholders}) "  # nosec B608
                f"ON CONFLICT (session_id, player_id) DO UPDATE SET {updates}",
                values,
            )
        return len(rows)
