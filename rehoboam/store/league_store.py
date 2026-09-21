"""Market snapshots, manager squads, fixtures, the table and the clubs (spec G1).

Snapshots are append-only and keyed by `snapshot_at`; readers take the newest.
One transaction per call, bulk `executemany`, no per-row round trips.
"""

from __future__ import annotations

from typing import Any

_LISTING_COLUMNS = (
    "snapshot_at",
    "player_id",
    "ask",
    "market_value",
    "mv_trend",
    "seller_id",
    "offer_count",
    "our_bid",
    "listed_at",
    "expires_at",
    "status",
    "lineup_probability",
    "source",
)
_SQUAD_COLUMNS = (
    "snapshot_at",
    "manager_id",
    "player_id",
    "market_value",
    "gain_loss",
    "on_market",
    "source",
)
_MANAGER_COLUMNS = ("manager_id", "league_id", "name", "is_self", "updated_at")
_FIXTURE_COLUMNS = (
    "match_id",
    "season",
    "day_number",
    "kickoff",
    "home_team_id",
    "away_team_id",
    "home_goals",
    "away_goals",
    "status",
    "updated_at",
)
_TABLE_COLUMNS = (
    "season",
    "day_number",
    "team_id",
    "place",
    "previous_place",
    "points",
    "played",
    "goal_difference",
    "updated_at",
)
_TEAM_COLUMNS = ("team_id", "name", "short_name", "updated_at", "crest_source")


def _upsert_sql(table: str, columns: tuple[str, ...], key: tuple[str, ...]) -> str:
    cols = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in key)
    # nosec B608 -- identifiers come from the module's constant tuples; values are %s params.
    return (
        f"INSERT INTO rehoboam.{table} ({cols}) VALUES ({placeholders}) "  # nosec B608
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}"
    )


class LeagueStore:
    _TABLE_ORDER = {
        "predicted_ep",
        "points",
        "avg_points",
        "market_value",
        "points_per_million",
        "trend_24h_pct",
        "trend_7d_pct",
        "fair_value_gap",
        "name",
    }

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def _write(self, sql: str, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(sql, [[r[c] for c in columns] for r in rows])
        return len(rows)

    def write_listings(self, rows: list[dict[str, Any]]) -> int:
        return self._write(
            _upsert_sql("market_listings", _LISTING_COLUMNS, ("snapshot_at", "player_id")),
            _LISTING_COLUMNS,
            rows,
        )

    def write_squads(self, rows: list[dict[str, Any]]) -> int:
        return self._write(
            _upsert_sql(
                "manager_squads",
                _SQUAD_COLUMNS,
                ("snapshot_at", "manager_id", "player_id"),
            ),
            _SQUAD_COLUMNS,
            rows,
        )

    def upsert_managers(self, rows: list[dict[str, Any]]) -> int:
        return self._write(
            _upsert_sql("managers", _MANAGER_COLUMNS, ("manager_id",)),
            _MANAGER_COLUMNS,
            rows,
        )

    def upsert_fixtures(self, rows: list[dict[str, Any]]) -> int:
        return self._write(
            _upsert_sql("fixtures", _FIXTURE_COLUMNS, ("match_id",)),
            _FIXTURE_COLUMNS,
            rows,
        )

    def write_table(self, rows: list[dict[str, Any]]) -> int:
        return self._write(
            _upsert_sql("league_table", _TABLE_COLUMNS, ("season", "day_number", "team_id")),
            _TABLE_COLUMNS,
            rows,
        )

    def upsert_teams(self, rows: list[dict[str, Any]]) -> int:
        return self._write(_upsert_sql("teams", _TEAM_COLUMNS, ("team_id",)), _TEAM_COLUMNS, rows)

    def latest_snapshot(self, table: str) -> float | None:
        if table not in ("market_listings", "manager_squads"):
            raise ValueError(table)
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT MAX(snapshot_at) AS at FROM rehoboam.{table}"  # nosec B608 -- whitelisted above
            ).fetchone()
        return float(row["at"]) if row and row["at"] is not None else None

    def latest_market(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rehoboam.market_listings "
                "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM rehoboam.market_listings) "
                "ORDER BY player_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def owner_of(self, player_ids: list[str]) -> dict[str, str]:
        """Manager name from each manager's own newest squad snapshot, else 'market'
        from the newest market snapshot. Players in neither are absent (callers read
        'Kickbase'). Newest is resolved per manager, not globally, so one manager's
        stale or missing snapshot cannot blank another manager's ownership."""
        ids = [str(p) for p in player_ids]
        if not ids:
            return {}
        with self.connection() as conn:
            owned = conn.execute(
                "WITH newest AS ("
                "  SELECT manager_id, MAX(snapshot_at) AS at"
                "  FROM rehoboam.manager_squads GROUP BY manager_id"
                ") "
                "SELECT s.player_id, m.name FROM rehoboam.manager_squads s "
                "JOIN newest n ON n.manager_id = s.manager_id AND n.at = s.snapshot_at "
                "JOIN rehoboam.managers m ON m.manager_id = s.manager_id "
                "WHERE s.player_id = ANY(%s)",
                (ids,),
            ).fetchall()
            listed = conn.execute(
                "SELECT player_id FROM rehoboam.market_listings "
                "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM rehoboam.market_listings) "
                "AND player_id = ANY(%s)",
                (ids,),
            ).fetchall()
        out = {r["player_id"]: r["name"] for r in owned}
        for r in listed:
            out.setdefault(r["player_id"], "market")
        return out

    def teams_older_than(self, epoch: float) -> list[str]:
        """Club ids in the universe with no `teams` row or one updated before `epoch`."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT u.team_id FROM rehoboam.player_universe u "
                "LEFT JOIN rehoboam.teams t ON t.team_id = u.team_id "
                "WHERE u.team_id IS NOT NULL AND (t.team_id IS NULL OR t.updated_at < %s) "
                "ORDER BY u.team_id",
                (epoch,),
            ).fetchall()
        return [r["team_id"] for r in rows]

    def player_table(
        self,
        *,
        position: str | None = None,
        owner: str | None = None,
        order_by: str = "predicted_ep",
    ) -> list[dict[str, Any]]:
        """The Base XI player table view, optionally filtered and ordered.

        `order_by` is whitelisted against `_TABLE_ORDER`; the WHERE clause is
        built from constant fragments only, values pass as `%s` parameters.
        """
        if order_by not in self._TABLE_ORDER:
            raise ValueError(f"order_by must be one of {sorted(self._TABLE_ORDER)}")
        clauses, params = [], []
        if position:
            clauses.append("position = %s")
            params.append(position)
        if owner:
            clauses.append("owner = %s")
            params.append(owner)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as conn:
            rows = conn.execute(
                # nosec B608 -- `where` is built from constant fragments, `order_by` is whitelisted.
                f"SELECT * FROM rehoboam.player_table {where} "  # nosec B608
                f"ORDER BY {order_by} DESC NULLS LAST, player_id",
                params,
            ).fetchall()
        return [dict(r) for r in rows]
