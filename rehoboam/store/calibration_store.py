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

    def last_predictions_before(
        self, *, season: str, day_number: int, kickoff: float, backfill: bool
    ) -> dict[str, dict[str, Any]]:
        """Per player, the newest prediction for this matchday made before `kickoff`."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (player_id) * FROM rehoboam.predictions "
                "WHERE season = %s AND day_number = %s AND predicted_at < %s AND backfill = %s "
                "ORDER BY player_id, predicted_at DESC",
                (season, day_number, kickoff, backfill),
            ).fetchall()
        return {r["player_id"]: dict(r) for r in rows}

    def squad_before(
        self, *, season: str, day_number: int, kickoff: float
    ) -> tuple[set[str], set[str]]:
        """`(owned_ids, fielded_ids)` from the newest non-dry-run, non-backfill session
        before kickoff — the same session for both, so `owned` and the fielded eleven
        can never come from two different sessions."""
        with self.connection() as conn:
            session = conn.execute(
                "SELECT session_id FROM rehoboam.predictions "
                "WHERE season = %s AND day_number = %s AND predicted_at < %s "
                "AND dry_run = false AND backfill = false "
                "ORDER BY predicted_at DESC LIMIT 1",
                (season, day_number, kickoff),
            ).fetchone()
            if not session:
                return set(), set()
            rows = conn.execute(
                "SELECT player_id, in_best_11 FROM rehoboam.predictions "
                "WHERE session_id = %s AND owned",
                (session["session_id"],),
            ).fetchall()
        owned_ids = {r["player_id"] for r in rows}
        fielded_ids = {r["player_id"] for r in rows if r["in_best_11"]}
        return owned_ids, fielded_ids

    def actuals_for(self, *, season: str, day_number: int) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT h.player_id, h.points, h.minutes, h.status, h.team_id, u.position, "
                "COALESCE(u.last_name, h.player_id) AS name "
                "FROM rehoboam.player_match_history h "
                "JOIN rehoboam.player_universe u ON u.player_id = h.player_id "
                "WHERE h.season = %s AND h.day_number = %s AND u.position IS NOT NULL "
                "ORDER BY h.player_id",
                (season, day_number),
            ).fetchall()
        return [dict(r) for r in rows]

    def history_before(
        self, *, before_iso: str, player_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Every match row dated strictly before `before_iso`, per player, oldest first —
        restricted to `player_ids` (the players a caller actually needs)."""
        if not player_ids:
            return {}
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, season, day_number, match_date, points, minutes, status "
                "FROM rehoboam.player_match_history "
                "WHERE match_date < %s AND player_id = ANY(%s) "
                "ORDER BY player_id, season, day_number",
                (before_iso, player_ids),
            ).fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r["player_id"], []).append(dict(r))
        return out

    def players_needing_final_rows(
        self, *, season: str, day_number: int, whistle: float, live_since: float | None = None
    ) -> list[str]:
        """Players with a row for this matchday whose performance was last fetched
        before `whistle` — every pre-whistle player when `live_since` is None; with
        `live_since` given, only the ones whose status has ALSO been fetched since
        then (the live, still-refetchable subset — anyone else has left the live
        universe and can never be refetched, so he cannot hold the report back,
        even though his stale row still counts and is excluded from the report)."""
        if live_since is None:
            query = (
                "SELECT h.player_id FROM rehoboam.player_match_history h "
                "LEFT JOIN rehoboam.sweep_progress p ON p.player_id = h.player_id "
                "WHERE h.season = %s AND h.day_number = %s "
                "AND (p.performance_fetched_at IS NULL OR p.performance_fetched_at < %s) "
                "ORDER BY h.player_id"
            )
            params = (season, day_number, whistle)
        else:
            query = (
                "SELECT h.player_id FROM rehoboam.player_match_history h "
                "LEFT JOIN rehoboam.sweep_progress p ON p.player_id = h.player_id "
                "WHERE h.season = %s AND h.day_number = %s "
                "AND (p.performance_fetched_at IS NULL OR p.performance_fetched_at < %s) "
                "AND EXISTS (SELECT 1 FROM rehoboam.player_status_daily s "
                "WHERE s.player_id = h.player_id AND s.fetched_at >= %s) "
                "ORDER BY h.player_id"
            )
            params = (season, day_number, whistle, live_since)
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [r["player_id"] for r in rows]

    def write_calibration(
        self,
        *,
        season: str,
        day_number: int,
        backfill: bool,
        rows: list[dict[str, Any]],
        report,
        gate: dict[str, Any] | None,
        computed_at: float,
        telegram_sent: bool = False,
    ) -> None:
        """Replace this matchday's rows and report in one transaction."""
        r = report.as_row()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rehoboam.calibration_rows "
                "WHERE season = %s AND day_number = %s AND backfill = %s",
                (season, day_number, backfill),
            )
            cur.executemany(
                "INSERT INTO rehoboam.calibration_rows (season, day_number, player_id, backfill, "
                "session_id, predicted_ep, live_ep, baseline_ep, actual_points, minutes, status, "
                "position, team_id, owned, in_best_11, prev_status, live_status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        season,
                        day_number,
                        x["player_id"],
                        backfill,
                        x["session_id"],
                        x["predicted_ep"],
                        x["live_ep"],
                        x["baseline_ep"],
                        x["actual_points"],
                        x["minutes"],
                        x["status"],
                        x["position"],
                        x["team_id"],
                        x["owned"],
                        x["in_best_11"],
                        x["prev_status"],
                        x["live_status"],
                    )
                    for x in rows
                ],
            )
            cur.execute(
                "INSERT INTO rehoboam.calibration_reports (season, day_number, backfill, "
                "computed_at, n, n_unpredicted, n_stale_rows, mae, bias, spearman, "
                "baseline_spearman, spearman_played, top11_regret, baseline_top11_regret, "
                "squad_regret, live_spearman, live_n, by_position, by_status, worst, gate, "
                "telegram_sent) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s) "
                "ON CONFLICT (season, day_number, backfill) DO UPDATE SET "
                "computed_at = excluded.computed_at, n = excluded.n, "
                "n_unpredicted = excluded.n_unpredicted, n_stale_rows = excluded.n_stale_rows, "
                "mae = excluded.mae, bias = excluded.bias, spearman = excluded.spearman, "
                "baseline_spearman = excluded.baseline_spearman, "
                "spearman_played = excluded.spearman_played, "
                "top11_regret = excluded.top11_regret, "
                "baseline_top11_regret = excluded.baseline_top11_regret, "
                "squad_regret = excluded.squad_regret, live_spearman = excluded.live_spearman, "
                "live_n = excluded.live_n, by_position = excluded.by_position, "
                "by_status = excluded.by_status, worst = excluded.worst, gate = excluded.gate, "
                "telegram_sent = excluded.telegram_sent",
                (
                    season,
                    day_number,
                    backfill,
                    computed_at,
                    r["n"],
                    r["n_unpredicted"],
                    r["n_stale_rows"],
                    r["mae"],
                    r["bias"],
                    r["spearman"],
                    r["baseline_spearman"],
                    r["spearman_played"],
                    r["top11_regret"],
                    r["baseline_top11_regret"],
                    r["squad_regret"],
                    r["live_spearman"],
                    r["live_n"],
                    Jsonb(r["by_position"]),
                    Jsonb(r["by_status"]),
                    Jsonb(r["worst"]),
                    Jsonb(gate) if gate is not None else None,
                    telegram_sent,
                ),
            )

    def report_for(
        self, season: str, day_number: int, *, backfill: bool = False
    ) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM rehoboam.calibration_reports "
                "WHERE season = %s AND day_number = %s AND backfill = %s",
                (season, day_number, backfill),
            ).fetchone()
        return dict(row) if row else None

    def recent_reports(self, season: str, *, backfill: bool = False) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rehoboam.calibration_reports "
                "WHERE season = %s AND backfill = %s ORDER BY day_number",
                (season, backfill),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_report(self, season: str, day_number: int, *, backfill: bool = False) -> None:
        """Drop this matchday's rows and report in one transaction (a backfill re-run
        rebuilds them from scratch rather than layering on top of a stale report)."""
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rehoboam.calibration_rows "
                "WHERE season = %s AND day_number = %s AND backfill = %s",
                (season, day_number, backfill),
            )
            cur.execute(
                "DELETE FROM rehoboam.calibration_reports "
                "WHERE season = %s AND day_number = %s AND backfill = %s",
                (season, day_number, backfill),
            )

    def mark_telegram_sent(self, season: str, day_number: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE rehoboam.calibration_reports SET telegram_sent = true "
                "WHERE season = %s AND day_number = %s AND backfill = false",
                (season, day_number),
            )

    def last_integrity_failure_at(self) -> float | None:
        """Newest integrity failure from a non-dry-run session (the gate's clean window)."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(f.created_at) AS at FROM rehoboam.integrity_failures f "
                "JOIN rehoboam.session_facts s ON s.session_id = f.session_id "
                "WHERE s.dry_run = 0"
            ).fetchone()
        return float(row["at"]) if row and row["at"] is not None else None
