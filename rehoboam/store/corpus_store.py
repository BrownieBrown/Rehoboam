"""The store-backed corpus writer: what the ingestion and the sweep write.

Same rows as the SQLite ``TrainingCorpus`` (they share ``enrichment.rows``),
but in ``rehoboam.*``. The SQLite class stays the offline reader; this one
is where the league-wide data lands from now on (spec §2).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
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
        self._pinned = None

    @contextmanager
    def session(self):
        """Pin one connection for a whole run.

        Each method still runs as its own top-level transaction (autocommit on,
        `conn.transaction()` per call), so a crash mid-run keeps every write that
        already committed — but the TLS + SCRAM handshake happens once, not
        twice per kind per player. Restores whatever was pinned before on
        exit, so a re-entered session can't null out an outer one's pin.
        """
        from rehoboam.store import connect

        previous = self._pinned
        with connect(self.dsn) as conn:
            conn.autocommit = True
            self._pinned = conn
            try:
                yield conn
            finally:
                self._pinned = previous

    @contextmanager
    def _pinned_transaction(self):
        with self._pinned.transaction():
            yield self._pinned

    def connection(self):
        if self._pinned is not None:
            return self._pinned_transaction()

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
                    player_id, day, status, lineup_probability, market_value, mv_change,
                    team_id, fetched_at, goals, assists, yellow_cards, red_cards,
                    seconds_played, season_points, season_average
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, day) DO UPDATE SET
                    status = excluded.status,
                    lineup_probability = excluded.lineup_probability,
                    market_value = excluded.market_value,
                    mv_change = excluded.mv_change,
                    team_id = excluded.team_id,
                    fetched_at = excluded.fetched_at,
                    goals = excluded.goals,
                    assists = excluded.assists,
                    yellow_cards = excluded.yellow_cards,
                    red_cards = excluded.red_cards,
                    seconds_played = excluded.seconds_played,
                    season_points = excluded.season_points,
                    season_average = excluded.season_average
                """,
                (
                    r["player_id"],
                    r["day"],
                    r["status"],
                    r["lineup_probability"],
                    r["market_value"],
                    r["mv_change"],
                    r["team_id"],
                    r["fetched_at"],
                    r["goals"],
                    r["assists"],
                    r["yellow_cards"],
                    r["red_cards"],
                    r["seconds_played"],
                    r["season_points"],
                    r["season_average"],
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
        at: float | None = None,
    ) -> None:
        """Record progress so an interrupted run resumes where it stopped.

        ``at`` defaults to wall-clock ``time.time()``; ``run_ingestion`` passes
        ``budget.now()`` instead so a test's fake clock, not the real one,
        determines what counts as stale on the run's next pass.
        """
        now = at if at is not None else time.time()
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

    def players_needing_any_refresh(
        self, older_than: dict[str, float], *, player_ids: list[str] | None = None
    ) -> list[tuple[str, list[str]]]:
        """Players with at least one stale kind, ordered by their stalest STALE kind.

        A player's sort key is the oldest of only its *stale* fetch times —
        ``least(case when ... end, ...)`` rather than ``least(coalesce(...))``,
        because Postgres ``least`` ignores NULLs: a fresh kind's ``case``
        evaluates to NULL and drops out of the comparison instead of pulling
        the player forward with a fabricated 0. Never fetched still counts as
        oldest. A budgeted run that stops mid-list resumes next time with
        exactly the players it did not reach. Each kind carries its own
        window: MV series change slowly and refresh weekly, status and
        performance daily.

        ``player_ids``, when given, restricts to that set — ``player_universe``
        also holds players no longer in any live squad (the historical
        corpus), and a refresh pass has no reason to spend budget on them.
        Folded into the same WHERE as the staleness check, on the one and
        only SELECT: a subquery's ORDER BY is not guaranteed by Postgres to
        survive an outer filter, so restricting rows and ordering them both
        have to happen on this query's own, outermost ORDER BY.
        """
        kinds = [k for k in ("status", "performance", "mv", "transfers") if k in older_than]
        if not kinds:
            return []
        cols = [_PROGRESS_COLUMNS[k] for k in kinds]
        select_cols = ", ".join(f"s.{c}" for c in cols)
        stale_clause = " OR ".join(f"s.{c} IS NULL OR s.{c} < %s" for c in cols)
        order_terms = ", ".join(
            f"case when s.{c} is null or s.{c} < %s then coalesce(s.{c}, 0) end" for c in cols
        )
        # Placeholder order must match the query text: WHERE windows, then
        # player_ids (also in WHERE, right after), then the ORDER BY's own
        # second binding of the same windows.
        params: list[Any] = [older_than[k] for k in kinds]
        where_clause = f"({stale_clause})"
        if player_ids is not None:
            where_clause += " AND u.player_id = ANY(%s)"
            params.append([str(p) for p in player_ids])
        params.extend(older_than[k] for k in kinds)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT u.player_id, {select_cols}
                FROM rehoboam.player_universe u
                LEFT JOIN rehoboam.sweep_progress s ON s.player_id = u.player_id
                WHERE {where_clause}
                ORDER BY least({order_terms}) ASC, u.player_id
                """,
                params,
            ).fetchall()
        out: list[tuple[str, list[str]]] = []
        for r in rows:
            stale = [
                k for k, c in zip(kinds, cols, strict=True) if r[c] is None or r[c] < older_than[k]
            ]
            out.append((r["player_id"], stale))
        return out

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
                "SELECT player_id, day, status, lineup_probability, market_value, mv_change, "
                "team_id, fetched_at, goals, assists, yellow_cards, red_cards, "
                "seconds_played, season_points, season_average "
                "FROM rehoboam.player_status_daily "
                "WHERE player_id = %s AND day = %s",
                (str(player_id), day),
            ).fetchone()
        return dict(row) if row else None
