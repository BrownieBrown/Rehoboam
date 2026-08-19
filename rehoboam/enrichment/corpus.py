"""Durable training corpus for the v2 scorer.

Deliberately NOT ``value_history.performance_cache``: that table is a
6-hour TTL cache with a 7-day cleanup path (``value_history.py:171``).
Training data has to outlive both, so it gets its own database.

Schema is append-mostly and idempotent — the league-wide sweep in
``sweep.py`` is long-running, gets interrupted, and must be resumable.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
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
    -- Availability label from the performance payload's per-match `st` field
    -- (1=not in squad, 3=came on as sub, 4=unused sub, 5=started; 0 for
    -- unplayed future fixtures; NULL when the payload omits it entirely).
    -- Named `status`, NOT `st` -- `st` already means something completely
    -- different elsewhere in this codebase: the *live* injury-status field
    -- returned by get_player_details (kickbase_client.py, matchup_analyzer.py,
    -- scorer.py). Do not conflate the two.
    status           INTEGER,
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
    mv_fetched_at          REAL,
    transfers_fetched_at   REAL
);

-- Real per-transaction transfer prices (REH-55), the only local source of a
-- season-wide reconstructed market -- market_prices.db is empty and
-- bid_learning.db.league_transfers covers five weeks, not nine months.
-- ``transfer_type`` (the raw ``t`` field) is stored as-is: its meaning was
-- not confirmed by the live probe (both 0 and 2 observed, 0 co-occurring
-- with price 0), so it is deliberately not interpreted here.
CREATE TABLE IF NOT EXISTS player_transfers (
    player_id         TEXT NOT NULL,
    transfer_at        REAL NOT NULL,
    price              INTEGER,
    transfer_type      INTEGER,
    counterparty_id    TEXT,
    counterparty_name  TEXT,
    PRIMARY KEY (player_id, transfer_at)
);

CREATE INDEX IF NOT EXISTS idx_player_transfers_player
    ON player_transfers(player_id);
"""


def _to_epoch(iso_str: str) -> float:
    """Convert an ISO-8601 timestamp (optional trailing 'Z') to a unix epoch float.

    Same convention as ``backfill.py:_to_epoch`` / ``baseline_driver.py:_to_epoch``
    -- not imported from either to avoid a cross-module dependency for one line,
    but must stay behaviorally identical to them.
    """
    if iso_str.endswith("Z"):
        iso_str = iso_str[:-1] + "+00:00"
    return datetime.fromisoformat(iso_str).timestamp()


class TrainingCorpus:
    """Read/write access to the training corpus database."""

    def __init__(self, db_path: Path | None = None, *, read_only: bool = False):
        """Open the corpus, creating and migrating its schema unless ``read_only``.

        ``read_only=True`` skips the whole construction-time write block --
        ``mkdir``, ``executescript(_SCHEMA)``, ``_migrate`` and ``commit`` --
        and requires the file to already exist. Both halves matter to a caller
        that pins its inputs by digest (REH-75's ``diagnose-flips``): ``_migrate``
        issues ``ALTER TABLE`` when a column is missing, so a read-write
        construction can mutate the very database a report cites the SHA-256 of,
        and the ``CREATE TABLE`` half silently manufactures an empty corpus from
        a mistyped path, which then produces a plausible all-zero report instead
        of an error.
        """
        if db_path is None:
            db_path = DEFAULT_CORPUS_PATH
        self.db_path = db_path
        self.read_only = read_only
        if read_only:
            if not db_path.exists():
                raise FileNotFoundError(f"corpus database not found: {db_path}")
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive migrations for databases created before a column existed.

        ``CREATE TABLE IF NOT EXISTS`` in ``_SCHEMA`` only helps a brand-new
        database -- an existing ``player_match_history`` table (75,890 rows in
        prod as of REH-52) keeps whatever columns it had when it was first
        created, so a column added to ``_SCHEMA`` later needs an explicit
        ``ALTER TABLE`` here too. Guarded by a ``PRAGMA table_info`` check so
        it is a no-op both for a fresh DB (which already has the column via
        ``CREATE TABLE``) and for a DB that has already been migrated.
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(player_match_history)")}
        if "status" not in columns:
            conn.execute("ALTER TABLE player_match_history ADD COLUMN status INTEGER")

        sweep_columns = {row[1] for row in conn.execute("PRAGMA table_info(sweep_progress)")}
        if "transfers_fetched_at" not in sweep_columns:
            conn.execute("ALTER TABLE sweep_progress ADD COLUMN transfers_fetched_at REAL")

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

    def players_missing_position(self, player_ids: list[str]) -> list[str]:
        """Subset of ``player_ids`` that still need a position resolved.

        An id is "missing" if it has no ``player_universe`` row at all, or
        the row exists but ``position IS NULL`` — the state every stub from
        ``ensure_players`` starts in. Once a position is set (by any path —
        ``upsert_players`` or otherwise), the id drops out of this list for
        good, which is what makes the historical-player position-resolution
        pass in ``sweep.run_sweep`` resumable: a rerun only retries ids that
        are still unresolved, never ones that already succeeded.
        """
        ids = {str(pid) for pid in player_ids}
        if not ids:
            return []
        with sqlite3.connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in ids)
            resolved = {
                row[0]
                for row in conn.execute(
                    "SELECT player_id FROM player_universe "
                    f"WHERE player_id IN ({placeholders}) AND position IS NOT NULL",
                    list(ids),
                )
            }
        return sorted(ids - resolved)

    def positions_for(self, player_ids: list[str]) -> dict[str, str]:
        """Resolved ``position`` for each id, keyed by player_id.

        Ids with no ``player_universe`` row, or whose position was never
        resolved (``position IS NULL`` — the state every ``ensure_players``
        stub starts in), are simply absent from the returned dict. Callers
        that need a squad's position shape (e.g. the backtest baseline
        driver joining reconstructed squad ids against the corpus) can then
        treat "missing" and "unresolved" identically by using ``.get``.
        """
        ids = {str(pid) for pid in player_ids}
        if not ids:
            return {}
        with sqlite3.connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT player_id, position FROM player_universe "
                f"WHERE player_id IN ({placeholders}) AND position IS NOT NULL",
                list(ids),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def record_match_history(
        self, player_id: str, team_id: str | None, performance: dict[str, Any]
    ) -> int:
        """Flatten a performance response into per-match rows.

        Shape: ``{"it": [{"ti": "2025/2026", "ph": [{...match...}]}]}``.
        Matches without a ``day`` are skipped — they cannot be placed on a
        timeline and so are useless for both training and backtesting.

        Each match's ``pt`` field is the player's team *for that match* —
        verified to stay constant for a player within a season while
        ``t1``/``t2`` alternate. It is the primary source for is_home,
        opponent_team_id and the stored team_id, because the caller-supplied
        ``team_id`` reflects only the player's *current* team (e.g.
        ``sweep.py``'s ``team_by_id``, built from the live universe
        snapshot) and is wrong for every match before a player's most recent
        transfer — every 15-season history for a transferred player would
        otherwise mislabel home/away and record the player's own former team
        as the opponent. ``team_id`` is only a fallback for the rare match
        entry that omits ``pt``.

        Each match also carries an ``st`` field, stored as ``status`` (see
        the column comment in ``_SCHEMA`` for why it isn't named ``st``
        here). Deliberately NOT stored: ``ap``, ``tp``, ``asp``. They look
        like point-in-time per-match aggregates but are not — ``tp`` was
        verified live to be constant across an entire season (362 on every
        matchday for a sampled player), i.e. a season-end total stamped onto
        every row. Storing it would leak the season outcome into training
        rows for matchdays that hadn't happened yet. Do not re-add these
        three without re-verifying that finding.
        """
        rows: list[tuple] = []
        fallback_team = str(team_id) if team_id is not None else None

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
                pt = m.get("pt")
                team = str(pt) if pt is not None else fallback_team
                is_home = 1 if team is not None and team == t1 else 0
                opponent = t2 if is_home else t1
                st = m.get("st")
                status = int(st) if st is not None else None
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
                        status,
                    )
                )

        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO player_match_history (
                    player_id, season, day_number, match_date, points,
                    minutes, team_id, opponent_team_id, is_home, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def record_player_transfers(self, player_id: str, history: dict[str, Any]) -> int:
        """Persist a player's real transfer transactions (REH-55).

        Shape: ``{"it": [{"u": counterparty_id, "unm": counterparty_name,
        "dt": <ISO-8601 timestamp>, "trp": price, "t": transfer_type}]}``.

        Unlike ``mv_series``'s ``dt`` (days-since-epoch), this endpoint's
        ``dt`` is an ISO-8601 timestamp string -- verified against the live
        probe response (2026-07-29). Items missing ``dt`` cannot be placed on
        a timeline and are skipped, same rule as
        ``record_match_history``'s day-less matches.

        ``t`` (stored as ``transfer_type``) is persisted exactly as received
        and never interpreted here -- see
        ``KickbaseV4Client.get_player_transfer_history`` for the empirical
        read from the full REH-55 sweep (0/2/3 observed; only 2 carries a
        real price and is what the market reconstruction should use).
        """
        rows: list[tuple] = []
        for item in history.get("it") or []:
            dt = item.get("dt")
            if not dt:
                continue
            try:
                transfer_at = _to_epoch(dt)
            except (ValueError, TypeError):
                continue
            counterparty_id = item.get("u")
            rows.append(
                (
                    str(player_id),
                    transfer_at,
                    item.get("trp"),
                    item.get("t"),
                    str(counterparty_id) if counterparty_id is not None else None,
                    item.get("unm"),
                )
            )

        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO player_transfers (
                    player_id, transfer_at, price, transfer_type,
                    counterparty_id, counterparty_name
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        return len(rows)

    def transfers_for_player(self, player_id: str) -> list[dict[str, Any]]:
        """All recorded transfer transactions for a player, oldest first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT player_id, transfer_at, price, transfer_type,
                       counterparty_id, counterparty_name
                FROM player_transfers
                WHERE player_id = ?
                ORDER BY transfer_at
                """,
                (str(player_id),),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_fetched(
        self,
        player_id: str,
        *,
        performance: bool = False,
        mv: bool = False,
        transfers: bool = False,
    ) -> None:
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
            if transfers:
                conn.execute(
                    "UPDATE sweep_progress SET transfers_fetched_at = ? WHERE player_id = ?",
                    (now, str(player_id)),
                )
            conn.commit()

    def clear_performance_fetched(self, player_ids: list[str] | None = None) -> int:
        """Reset ``performance_fetched_at`` so the next sweep re-fetches it.

        Deliberately narrow: only the ``performance`` column is touched —
        ``mv_fetched_at`` (and thus MV-series resumability) is untouched, so
        this cannot turn into an accidental full re-fetch of MV history too.
        Used one-off after a bug in ``record_match_history`` (the
        ``is_home``/``opponent_team_id`` derivation) to force re-fetch of
        already-"complete" players, since ``players_needing_fetch`` would
        otherwise skip every one of them.

        ``player_ids=None`` clears every tracked player; passing an explicit
        list scopes the reset (e.g. to a single player during testing).

        Returns the number of rows actually cleared (rows that had a
        non-NULL ``performance_fetched_at`` before this call).
        """
        with sqlite3.connect(self.db_path) as conn:
            if player_ids is None:
                cur = conn.execute(
                    "UPDATE sweep_progress SET performance_fetched_at = NULL "
                    "WHERE performance_fetched_at IS NOT NULL"
                )
            else:
                ids = [str(pid) for pid in player_ids]
                if not ids:
                    return 0
                placeholders = ",".join("?" for _ in ids)
                cur = conn.execute(
                    f"UPDATE sweep_progress SET performance_fetched_at = NULL "
                    f"WHERE performance_fetched_at IS NOT NULL AND player_id IN ({placeholders})",
                    ids,
                )
            conn.commit()
            return cur.rowcount

    def players_needing_fetch(self, kind: str) -> list[str]:
        """Universe players with no successful fetch of ``kind`` yet.

        ``kind`` is ``"performance"``, ``"mv"``, or ``"transfers"``.
        """
        column = {
            "performance": "performance_fetched_at",
            "mv": "mv_fetched_at",
            "transfers": "transfers_fetched_at",
        }[kind]
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
                       team_id, opponent_team_id, is_home, status
                FROM player_match_history
                WHERE player_id = ?
                ORDER BY season, day_number
                """,
                (str(player_id),),
            ).fetchall()
        return [dict(r) for r in rows]

    def transfers_between(
        self, lo: float, hi: float, *, transfer_type: int = 2
    ) -> list[dict[str, Any]]:
        """Transactions with ``lo <= transfer_at <= hi``, oldest first.

        Defaults to ``transfer_type=2`` — the only type carrying a real price.
        Types 0 (season assignment) and 3 (release to pool) always have price 0.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                    SELECT player_id, transfer_at, price, transfer_type,
                           counterparty_id, counterparty_name
                    FROM player_transfers
                    WHERE transfer_type = ? AND transfer_at >= ? AND transfer_at <= ?
                    ORDER BY transfer_at
                    """,
                (transfer_type, lo, hi),
            ).fetchall()
        return [dict(r) for r in rows]

    def market_value_at(self, player_id: str, at: float) -> int | None:
        """Most recent market value at or before ``at``. None if no such snapshot."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT market_value FROM mv_series WHERE player_id = ? "
                "AND snapshot_at <= ? ORDER BY snapshot_at DESC LIMIT 1",
                (str(player_id), at),
            ).fetchone()
        return int(row[0]) if row else None

    def team_ids_for(self, player_ids: list[str]) -> dict[str, str]:
        """Map player_id -> team_id for players that have one recorded."""
        if not player_ids:
            return {}
        placeholders = ",".join("?" * len(player_ids))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT player_id, team_id FROM player_universe "  # noqa: S608
                f"WHERE player_id IN ({placeholders}) AND team_id IS NOT NULL",
                [str(p) for p in player_ids],
            ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows}
