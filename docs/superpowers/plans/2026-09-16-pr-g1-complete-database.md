# PR G1: Market, Ownership, Fixtures and the Player Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The store keeps every market listing, every manager's squad, the fixtures, the league table and the club names, and exposes Base XI's player table as a SQL view with our predictions appended — without one new API call in the trading session.

**Architecture:** Pure row builders in `enrichment/rows.py`; one store module `store/league_store.py` (bulk writes, one transaction per call); the client remembers the raw market payload it already fetched and the trader keeps the manager squads it already fetched, so the session writes from memory; the ingestion run adds a `run_league_refresh` step (~35 requests) before its per-player loop; the view is plain SQL in migration 006.

**Tech Stack:** Python 3.12, psycopg 3, PostgreSQL views with `FILTER`/`DISTINCT ON`/`regr_slope`, pytest on a real PostgreSQL (`store_dsn`), Typer + Rich.

**Spec:** `docs/superpowers/specs/2026-09-16-complete-database-design.md` (G1 section; the payload field names there were probed live 2026-09-15).

## Global Constraints

- Every SQL statement schema-qualifies `rehoboam.<table>`; store methods run one transaction per call through `connect(self.dsn)`; no `+`-built SQL from anything but constants.
- The trading session makes **no new API call**; it writes from `ep_result["market_payload"]`, `ep_result["competitor_squads"]`, `ep_result["ranking_payload"]` and `ctx.squad`. Every new session write is best-effort (try/except, `logger.exception`).
- The ingestion refresh counts every request in the run's budget and never stops the per-player loop on a failure (per-call try/except, `failed` counter).
- Snapshots are append-only, keyed by `snapshot_at`; readers take the newest.
- Line length 100; black + ruff clean for the files you touch; format only your hunks in existing files (`trader.py`, `auto_trader.py`, `cli.py`, `ingest.py`, `rows.py`, `kickbase_client.py`, `api.py`).
- Tests on the database use `store_dsn`. Never read, print or edit `.env`; never connect to any non-test database; never run `rehoboam auto|status|ingest|calibrate|players|market|backfill-league|migrate`.
- A session hook blocks the `uv run` wrapper: use `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/black`, `.venv/bin/python` directly.
- Commit trailers, exactly: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01F4d1fpUbNh28evK1pNy4Le`. Never commit to `main`. Implementers never dispatch subagents.

______________________________________________________________________

### Task 1: Migration 005 and `LeagueStore`

**Files:**

- Create: `rehoboam/store/migrations/005_league_state.sql`
- Create: `rehoboam/store/league_store.py`
- Create: `tests/store/test_league_store.py`
- Modify: `tests/store/test_migrate.py` (`EXPECTED_TABLES` gains the six tables)

**Interfaces:**

- Produces: `LeagueStore(dsn=None)` with `write_listings(rows) -> int`, `write_squads(rows) -> int`, `upsert_managers(rows) -> int`, `upsert_fixtures(rows) -> int`, `write_table(rows) -> int`, `upsert_teams(rows) -> int`, `latest_market() -> list[dict]`, `owner_of(player_ids) -> dict[str, str]`, `teams_older_than(epoch) -> list[str]` (team ids in `player_universe` with no `teams` row or one older than `epoch`), `latest_snapshot(table) -> float | None`.

- Row dict keys are exactly the column names below.

- [ ] **Step 1: Write the migration**

```sql
-- G1: what is on the market, who owns whom, the fixtures, the table, the clubs.
create table if not exists rehoboam.market_listings (
    snapshot_at        double precision not null,
    player_id          text not null,
    ask                bigint not null,
    market_value       bigint,
    mv_trend           integer,
    seller_id          text,
    offer_count        integer,
    our_bid            bigint,
    listed_at          double precision,
    expires_at         double precision,
    status             integer,
    lineup_probability integer,
    source             text not null,
    primary key (snapshot_at, player_id)
);
create index if not exists idx_market_listings_player on rehoboam.market_listings (player_id, snapshot_at);

create table if not exists rehoboam.managers (
    manager_id  text primary key,
    league_id   text not null,
    name        text not null,
    is_self     boolean not null default false,
    updated_at  double precision not null
);

create table if not exists rehoboam.manager_squads (
    snapshot_at   double precision not null,
    manager_id    text not null,
    player_id     text not null,
    market_value  bigint,
    gain_loss     bigint,
    on_market     boolean,
    source        text not null,
    primary key (snapshot_at, manager_id, player_id)
);
create index if not exists idx_manager_squads_player on rehoboam.manager_squads (player_id, snapshot_at);
create index if not exists idx_manager_squads_manager on rehoboam.manager_squads (manager_id, snapshot_at);

create table if not exists rehoboam.fixtures (
    match_id      text primary key,
    season        text not null,
    day_number    integer not null,
    kickoff       double precision not null,
    home_team_id  text not null,
    away_team_id  text not null,
    home_goals    integer,
    away_goals    integer,
    status        integer not null,
    updated_at    double precision not null
);
create index if not exists idx_fixtures_day on rehoboam.fixtures (season, day_number);

create table if not exists rehoboam.league_table (
    season          text not null,
    day_number      integer not null,
    team_id         text not null,
    place           integer not null,
    previous_place  integer,
    points          integer,
    played          integer,
    goal_difference integer,
    updated_at      double precision not null,
    primary key (season, day_number, team_id)
);

create table if not exists rehoboam.teams (
    team_id     text primary key,
    name        text not null,
    short_name  text,
    updated_at  double precision not null
);
```

- [ ] **Step 2: Write the failing tests**

```python
"""Market snapshots, squads, fixtures, table and clubs in the store (G1 Task 1)."""

from __future__ import annotations

from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore

T0 = 1_789_600_000.0


def _listing(pid, snapshot_at=T0, **over):
    row = {
        "snapshot_at": snapshot_at,
        "player_id": pid,
        "ask": 5_000_000,
        "market_value": 4_900_000,
        "mv_trend": 1,
        "seller_id": None,
        "offer_count": 0,
        "our_bid": None,
        "listed_at": snapshot_at - 3600,
        "expires_at": snapshot_at + 80_000,
        "status": 0,
        "lineup_probability": 1,
        "source": "session",
    }
    row.update(over)
    return row


def _squad_row(manager_id, pid, snapshot_at=T0, **over):
    row = {
        "snapshot_at": snapshot_at,
        "manager_id": manager_id,
        "player_id": pid,
        "market_value": 1_000_000,
        "gain_loss": 50_000,
        "on_market": False,
        "source": "session",
    }
    row.update(over)
    return row


def test_listings_are_append_only_snapshots(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    assert store.write_listings([_listing("a"), _listing("b")]) == 2
    assert (
        store.write_listings([_listing("a", snapshot_at=T0 + 60, ask=5_100_000)]) == 1
    )
    assert store.write_listings([_listing("a")]) == 1  # same key: upsert, no error
    latest = store.latest_market()
    assert [r["player_id"] for r in latest] == ["a"] and latest[0]["ask"] == 5_100_000
    assert store.latest_snapshot("market_listings") == T0 + 60


def test_owner_precedence_manager_then_market_then_kickbase(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    store.upsert_managers(
        [
            {
                "manager_id": "m1",
                "league_id": "L",
                "name": "Marco",
                "is_self": True,
                "updated_at": T0,
            },
            {
                "manager_id": "m2",
                "league_id": "L",
                "name": "Rival",
                "is_self": False,
                "updated_at": T0,
            },
        ]
    )
    store.write_squads([_squad_row("m1", "a"), _squad_row("m2", "b")])
    store.write_squads(
        [_squad_row("m2", "c", snapshot_at=T0 + 60)]
    )  # newest snapshot wins
    store.write_listings([_listing("d")])
    assert store.owner_of(["a", "b", "c", "d", "e"]) == {"c": "Rival", "d": "market"}
    # `a`/`b` were in the older snapshot only; `e` is nowhere: both absent => "Kickbase" for callers


def test_upsert_managers_updates_the_name(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    store.upsert_managers(
        [
            {
                "manager_id": "m1",
                "league_id": "L",
                "name": "Old",
                "is_self": False,
                "updated_at": T0,
            }
        ]
    )
    store.upsert_managers(
        [
            {
                "manager_id": "m1",
                "league_id": "L",
                "name": "New",
                "is_self": False,
                "updated_at": T0 + 1,
            }
        ]
    )
    with store.connection() as conn:
        row = conn.execute(
            "SELECT name FROM rehoboam.managers WHERE manager_id = 'm1'"
        ).fetchone()
    assert row["name"] == "New"


def test_fixtures_upsert_by_match_id(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    fx = {
        "match_id": "1",
        "season": "2026/2027",
        "day_number": 4,
        "kickoff": T0,
        "home_team_id": "2",
        "away_team_id": "9",
        "home_goals": None,
        "away_goals": None,
        "status": 0,
        "updated_at": T0,
    }
    assert store.upsert_fixtures([fx]) == 1
    assert (
        store.upsert_fixtures(
            [dict(fx, home_goals=2, away_goals=1, status=2, updated_at=T0 + 9000)]
        )
        == 1
    )
    with store.connection() as conn:
        row = conn.execute(
            "SELECT status, home_goals FROM rehoboam.fixtures WHERE match_id='1'"
        ).fetchone()
    assert (row["status"], row["home_goals"]) == (2, 2)


def test_table_and_teams(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    CorpusStore(dsn=store_dsn).upsert_players(
        [
            {
                "player_id": "p",
                "first_name": None,
                "last_name": "P",
                "position": "Defender",
                "team_id": "7",
                "market_value": 1,
                "average_points": 1.0,
            },
            {
                "player_id": "q",
                "first_name": None,
                "last_name": "Q",
                "position": "Defender",
                "team_id": "8",
                "market_value": 1,
                "average_points": 1.0,
            },
        ]
    )
    assert sorted(store.teams_older_than(T0)) == ["7", "8"]
    store.upsert_teams(
        [{"team_id": "7", "name": "Club Seven", "short_name": "SEV", "updated_at": T0}]
    )
    assert store.teams_older_than(T0 - 1) == ["8"]
    assert store.teams_older_than(T0 + 1) == ["7", "8"]
    assert (
        store.write_table(
            [
                {
                    "season": "2026/2027",
                    "day_number": 3,
                    "team_id": "7",
                    "place": 1,
                    "previous_place": 2,
                    "points": 9,
                    "played": 3,
                    "goal_difference": 5,
                    "updated_at": T0,
                }
            ]
        )
        == 1
    )
    with store.connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM rehoboam.league_table").fetchone()[
            "n"
        ]
    assert n == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/store/test_league_store.py -q`
Expected: FAIL with `ModuleNotFoundError: rehoboam.store.league_store`.

- [ ] **Step 4: Write `rehoboam/store/league_store.py`**

```python
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
_TEAM_COLUMNS = ("team_id", "name", "short_name", "updated_at")


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
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def _write(
        self, sql: str, columns: tuple[str, ...], rows: list[dict[str, Any]]
    ) -> int:
        if not rows:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(sql, [[r[c] for c in columns] for r in rows])
        return len(rows)

    def write_listings(self, rows: list[dict[str, Any]]) -> int:
        return self._write(
            _upsert_sql(
                "market_listings", _LISTING_COLUMNS, ("snapshot_at", "player_id")
            ),
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
            _upsert_sql(
                "league_table", _TABLE_COLUMNS, ("season", "day_number", "team_id")
            ),
            _TABLE_COLUMNS,
            rows,
        )

    def upsert_teams(self, rows: list[dict[str, Any]]) -> int:
        return self._write(
            _upsert_sql("teams", _TEAM_COLUMNS, ("team_id",)), _TEAM_COLUMNS, rows
        )

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
        """Manager name from the newest squad snapshot, else 'market' from the newest
        market snapshot. Players in neither are absent (callers read 'Kickbase')."""
        ids = [str(p) for p in player_ids]
        if not ids:
            return {}
        with self.connection() as conn:
            owned = conn.execute(
                "SELECT s.player_id, m.name FROM rehoboam.manager_squads s "
                "JOIN rehoboam.managers m ON m.manager_id = s.manager_id "
                "WHERE s.snapshot_at = (SELECT MAX(snapshot_at) FROM rehoboam.manager_squads) "
                "AND s.player_id = ANY(%s)",
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
```

Add `market_listings`, `managers`, `manager_squads`, `fixtures`, `league_table`, `teams` to `EXPECTED_TABLES` in `tests/store/test_migrate.py` (and bump any simulated-migration filename that would now collide with `005`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/store/test_league_store.py tests/store/test_migrate.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add rehoboam/store/migrations/005_league_state.sql rehoboam/store/league_store.py tests/store/test_league_store.py tests/store/test_migrate.py
git commit -m "feat(store): market listings, manager squads, fixtures, table and clubs"
```

______________________________________________________________________

### Task 2: Pure row builders

**Files:**

- Modify: `rehoboam/enrichment/rows.py` (append)
- Create: `tests/test_enrichment/test_league_rows.py`

**Interfaces:**

- Produces: `market_listing_rows(payload, *, snapshot_at, our_user_id, source) -> list[dict]`, `manager_squad_rows(manager_id, payload_items, *, snapshot_at, source) -> list[dict]`, `own_squad_rows(manager_id, players, *, snapshot_at, source) -> list[dict]` (from `Player` objects), `manager_rows(ranking_payload, *, league_id, our_user_id, updated_at) -> list[dict]`, `fixture_rows(schedule_payload, *, season, updated_at) -> list[dict]`, `league_table_rows(payload, *, season, day_number, updated_at) -> list[dict]`, `team_row(profile_payload, *, updated_at) -> dict | None`. Row keys are the column names from Task 1.

- [ ] **Step 1: Write the failing tests**

```python
"""League payloads → rows, pure (G1 Task 2). Field names probed live 2026-09-15."""

from __future__ import annotations

from types import SimpleNamespace

from rehoboam.enrichment.rows import (
    fixture_rows,
    league_table_rows,
    manager_rows,
    manager_squad_rows,
    market_listing_rows,
    own_squad_rows,
    team_row,
)

T0 = 1_789_600_000.0
MARKET = {
    "it": [
        {
            "i": "11",
            "tid": "7",
            "pos": 2,
            "st": 0,
            "prob": 1,
            "mv": 4_254_977,
            "mvt": 2,
            "prc": 4_300_000,
            "ofc": 1,
            "exs": 22_965,
            "dt": "2026-09-15T16:05:06Z",
            "u": {"i": "999", "n": "Rival"},
            "uoid": "3616202",
            "uop": 4_400_000,
        },
        {
            "i": "12",
            "tid": "8",
            "pos": 4,
            "st": 1,
            "mv": 6_832_673,
            "prc": 6_832_673,
            "ofc": 0,
            "dt": "2026-09-15T11:41:16Z",
        },
        {"tid": "8"},  # no id: dropped
    ]
}


def test_market_listing_rows():
    rows = market_listing_rows(
        MARKET, snapshot_at=T0, our_user_id="3616202", source="session"
    )
    assert [r["player_id"] for r in rows] == ["11", "12"]
    a, b = rows
    assert a["ask"] == 4_300_000 and a["seller_id"] == "999" and a["offer_count"] == 1
    assert a["our_bid"] == 4_400_000 and a["expires_at"] == T0 + 22_965
    assert a["listed_at"] == 1_789_488_306.0  # 2026-09-15T16:05:06Z
    assert (
        a["mv_trend"] == 2 and a["lineup_probability"] == 1 and a["source"] == "session"
    )
    assert b["seller_id"] is None and b["expires_at"] is None and b["our_bid"] is None
    assert b["mv_trend"] is None and b["lineup_probability"] is None
    assert set(a) == {
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
    }


def test_our_bid_only_when_the_offer_holder_is_us():
    payload = {"it": [dict(MARKET["it"][0], uoid="777", uop=1)]}
    rows = market_listing_rows(
        payload, snapshot_at=T0, our_user_id="3616202", source="ingest"
    )
    assert rows[0]["our_bid"] is None


def test_manager_squad_rows():
    items = [
        {"pi": "11", "mv": 5_000_000, "mvgl": 250_000, "iotm": True, "pn": "X"},
        {"pi": "12", "mv": 1_000_000},
        {"mv": 3},  # no id: dropped
    ]
    rows = manager_squad_rows("m2", items, snapshot_at=T0, source="ingest")
    assert [(r["player_id"], r["gain_loss"], r["on_market"]) for r in rows] == [
        ("11", 250_000, True),
        ("12", None, None),
    ]
    assert rows[0]["manager_id"] == "m2" and rows[0]["source"] == "ingest"


def test_own_squad_rows_from_player_objects():
    players = [
        SimpleNamespace(id="11", market_value=5_000_000),
        SimpleNamespace(id="", market_value=1),
    ]
    rows = own_squad_rows("m1", players, snapshot_at=T0, source="session")
    assert rows == [
        {
            "snapshot_at": T0,
            "manager_id": "m1",
            "player_id": "11",
            "market_value": 5_000_000,
            "gain_loss": None,
            "on_market": None,
            "source": "session",
        }
    ]


def test_manager_rows():
    ranking = {
        "us": [
            {"i": "3616202", "n": "Marco", "tv": 1},
            {"i": "999", "n": "Rival"},
            {"n": "x"},
        ]
    }
    rows = manager_rows(ranking, league_id="L", our_user_id="3616202", updated_at=T0)
    assert [(r["manager_id"], r["name"], r["is_self"]) for r in rows] == [
        ("3616202", "Marco", True),
        ("999", "Rival", False),
    ]
    assert rows[0]["league_id"] == "L" and rows[0]["updated_at"] == T0
    assert manager_rows(
        {"it": ranking["us"]}, league_id="L", our_user_id="x", updated_at=T0
    )


def test_fixture_rows():
    schedule = {
        "it": [
            {
                "day": 3,
                "it": [
                    {
                        "mi": "1",
                        "dt": "2026-09-12T13:30:00Z",
                        "st": 2,
                        "t1": "2",
                        "t2": "9",
                        "t1g": 3,
                        "t2g": 1,
                    }
                ],
            },
            {
                "day": 4,
                "it": [
                    {
                        "mi": "2",
                        "dt": "2026-09-19T13:30:00Z",
                        "st": 0,
                        "t1": "5",
                        "t2": "6",
                    },
                    {"dt": "bad"},
                ],
            },
        ]
    }
    rows = fixture_rows(schedule, season="2026/2027", updated_at=T0)
    assert [
        (r["match_id"], r["day_number"], r["status"], r["home_goals"]) for r in rows
    ] == [("1", 3, 2, 3), ("2", 4, 0, None)]
    assert rows[0]["kickoff"] == 1_789_212_600.0 and rows[0]["season"] == "2026/2027"


def test_league_table_rows():
    table = {
        "it": [
            {"tid": "7", "tn": "Seven", "cpl": 1, "pcpl": 2, "cp": 9, "mc": 3, "gd": 5},
            {"cpl": 2},
        ]
    }
    rows = league_table_rows(table, season="2026/2027", day_number=3, updated_at=T0)
    assert rows == [
        {
            "season": "2026/2027",
            "day_number": 3,
            "team_id": "7",
            "place": 1,
            "previous_place": 2,
            "points": 9,
            "played": 3,
            "goal_difference": 5,
            "updated_at": T0,
        }
    ]


def test_team_row():
    assert team_row({"tid": "7", "tn": "Club Seven", "ts": "SEV"}, updated_at=T0) == {
        "team_id": "7",
        "name": "Club Seven",
        "short_name": "SEV",
        "updated_at": T0,
    }
    assert team_row({"tn": "no id"}, updated_at=T0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_enrichment/test_league_rows.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Append to `rehoboam/enrichment/rows.py`**

```python
def _iso_epoch(value) -> float | None:
    """ISO `...Z` (or offset) string → epoch seconds; None when unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def market_listing_rows(
    payload: dict, *, snapshot_at: float, our_user_id: str, source: str
) -> list[dict]:
    """`GET /market` → one row per listing. `exs` is seconds until expiry and is
    only present on manager-listed players; `uop` counts as our bid only when
    `uoid` is our user id."""
    rows: list[dict] = []
    for item in (payload or {}).get("it") or []:
        if not isinstance(item, dict) or not item.get("i"):
            continue
        seller = item.get("u")
        seller_id = (
            str(seller.get("i"))
            if isinstance(seller, dict) and seller.get("i")
            else None
        )
        ours = str(item.get("uoid") or "") == str(our_user_id)
        exs = item.get("exs")
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "player_id": str(item["i"]),
                "ask": int(item.get("prc") or item.get("mv") or 0),
                "market_value": _opt_int(item.get("mv")),
                "mv_trend": _opt_int(item.get("mvt")),
                "seller_id": seller_id,
                "offer_count": _opt_int(item.get("ofc")),
                "our_bid": _opt_int(item.get("uop")) if ours else None,
                "listed_at": _iso_epoch(item.get("dt")),
                "expires_at": (
                    snapshot_at + float(exs) if isinstance(exs, int | float) else None
                ),
                "status": _opt_int(item.get("st")),
                "lineup_probability": _opt_int(item.get("prob")),
                "source": source,
            }
        )
    return rows


def manager_squad_rows(
    manager_id: str, items: list, *, snapshot_at: float, source: str
) -> list[dict]:
    """`/managers/{mid}/squad` `it[]` → rows (`pi` id, `mvgl` gain/loss, `iotm` on the market)."""
    rows: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        pid = item.get("pi") or item.get("i")
        if not pid:
            continue
        on_market = item.get("iotm")
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "manager_id": str(manager_id),
                "player_id": str(pid),
                "market_value": _opt_int(item.get("mv")),
                "gain_loss": _opt_int(item.get("mvgl")),
                "on_market": bool(on_market) if on_market is not None else None,
                "source": source,
            }
        )
    return rows


def own_squad_rows(
    manager_id: str, players: list, *, snapshot_at: float, source: str
) -> list[dict]:
    """Our own squad from the session's `Player` objects (no gain/loss or market flag there)."""
    rows: list[dict] = []
    for p in players or []:
        pid = getattr(p, "id", None)
        if not pid:
            continue
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "manager_id": str(manager_id),
                "player_id": str(pid),
                "market_value": _opt_int(getattr(p, "market_value", None)),
                "gain_loss": None,
                "on_market": None,
                "source": source,
            }
        )
    return rows


def manager_rows(
    ranking: dict, *, league_id: str, our_user_id: str, updated_at: float
) -> list[dict]:
    """`/ranking` `us[]` (older payloads: `it[]`) → managers."""
    rows: list[dict] = []
    for m in (ranking or {}).get("us") or (ranking or {}).get("it") or []:
        if not isinstance(m, dict) or not m.get("i"):
            continue
        rows.append(
            {
                "manager_id": str(m["i"]),
                "league_id": str(league_id),
                "name": str(m.get("n") or m["i"]),
                "is_self": str(m["i"]) == str(our_user_id),
                "updated_at": updated_at,
            }
        )
    return rows


def fixture_rows(schedule: dict, *, season: str, updated_at: float) -> list[dict]:
    """`/competitions/1/matchdays` → one row per fixture with a match id and a parseable kickoff."""
    rows: list[dict] = []
    for group in (schedule or {}).get("it") or []:
        if not isinstance(group, dict) or not isinstance(group.get("day"), int):
            continue
        for f in group.get("it") or []:
            if not isinstance(f, dict) or not f.get("mi"):
                continue
            kickoff = _iso_epoch(f.get("dt"))
            if kickoff is None or f.get("t1") is None or f.get("t2") is None:
                continue
            rows.append(
                {
                    "match_id": str(f["mi"]),
                    "season": season,
                    "day_number": int(group["day"]),
                    "kickoff": kickoff,
                    "home_team_id": str(f["t1"]),
                    "away_team_id": str(f["t2"]),
                    "home_goals": _opt_int(f.get("t1g")),
                    "away_goals": _opt_int(f.get("t2g")),
                    "status": int(f.get("st") or 0),
                    "updated_at": updated_at,
                }
            )
    return rows


def league_table_rows(
    table: dict, *, season: str, day_number: int, updated_at: float
) -> list[dict]:
    """`/competitions/1/table` `it[]` → rows (`cpl` place, `pcpl` previous, `cp` points, `mc`, `gd`)."""
    rows: list[dict] = []
    for r in (table or {}).get("it") or []:
        if not isinstance(r, dict) or not r.get("tid") or r.get("cpl") is None:
            continue
        rows.append(
            {
                "season": season,
                "day_number": int(day_number),
                "team_id": str(r["tid"]),
                "place": int(r["cpl"]),
                "previous_place": _opt_int(r.get("pcpl")),
                "points": _opt_int(r.get("cp")),
                "played": _opt_int(r.get("mc")),
                "goal_difference": _opt_int(r.get("gd")),
                "updated_at": updated_at,
            }
        )
    return rows


def team_row(profile: dict, *, updated_at: float) -> dict | None:
    """`/teams/{tid}/teamprofile` → one `teams` row; None without an id."""
    if not isinstance(profile, dict) or not profile.get("tid"):
        return None
    return {
        "team_id": str(profile["tid"]),
        "name": str(profile.get("tn") or profile["tid"]),
        "short_name": str(profile["ts"]) if profile.get("ts") else None,
        "updated_at": updated_at,
    }
```

`rows.py` already has `_opt_int` and imports `datetime`/`date`; confirm and add what is missing. For the `listed_at` test value: `2026-09-15T16:05:06Z` is epoch 1789488306 (verify with `.venv/bin/python -c "from datetime import datetime, timezone; print(datetime(2026,9,15,16,5,6,tzinfo=timezone.utc).timestamp())"` and correct the test constant if the arithmetic differs; same for the fixture kickoff `2026-09-12T13:30:00Z`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_enrichment/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/enrichment/rows.py tests/test_enrichment/test_league_rows.py
git commit -m "feat(rows): market listings, squads, managers, fixtures, table and clubs as pure rows"
```

______________________________________________________________________

### Task 3: The `player_table` view

**Files:**

- Create: `rehoboam/store/migrations/006_player_table.sql`
- Modify: `rehoboam/store/league_store.py` (add `player_table(*, position=None, owner=None, order_by="predicted_ep") -> list[dict]`)
- Modify: `tests/store/test_migrate.py` (`EXPECTED_TABLES` gains `player_table` if `_tables` lists views)
- Create: `tests/store/test_player_table.py`

**Interfaces:**

- Produces: view `rehoboam.player_table` with columns, in order: `player_id, name, team, position, market_value, trend_24h_pct, trend_7d_pct, points, avg_points, median_points, points_per_million, points_prev, avg_points_prev, appearances, appearances_prev, starts, starts_prev, owner, predicted_ep, p_start, fair_value_gap`.

- [ ] **Step 1: Write the failing tests**

```python
"""Base XI's table as a view over the store (G1 Task 3)."""

from __future__ import annotations

import time
from datetime import date

from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore

NOW = time.time()


def _perf(seasons):
    return {"it": [{"ti": title, "ph": matches} for title, matches in seasons]}


def _m(day, st, p, md="2026-08-22T13:30:00Z"):
    return {
        "day": day,
        "md": md,
        "st": st,
        "p": p,
        "mp": "90",
        "t1": "1",
        "t2": "2",
        "pt": "1",
    }


def _seed(dsn):
    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [
            {
                "player_id": "a",
                "first_name": None,
                "last_name": "Alpha",
                "position": "Midfielder",
                "team_id": "7",
                "market_value": 10_000_000,
                "average_points": 50.0,
            },
            {
                "player_id": "b",
                "first_name": None,
                "last_name": "Beta",
                "position": "Midfielder",
                "team_id": "8",
                "market_value": 2_000_000,
                "average_points": 10.0,
            },
        ]
    )
    corpus.record_match_history(
        "a",
        "7",
        _perf(
            [
                (
                    "2025/2026",
                    [
                        _m(1, 5, 100, "2025-08-23T13:30:00Z"),
                        _m(2, 4, 0, "2025-08-30T13:30:00Z"),
                    ],
                ),
                (
                    "2026/2027",
                    [
                        _m(1, 5, 80),
                        _m(2, 3, 20, "2026-08-29T13:30:00Z"),
                        _m(3, 1, 0, "2026-09-12T13:30:00Z"),
                        _m(4, 0, 0, "2026-09-19T13:30:00Z"),
                    ],
                ),
            ]
        ),
    )
    corpus.record_match_history("b", "8", _perf([("2026/2027", [_m(1, 5, 10)])]))
    corpus.record_status_daily(
        "a", date.today(), {"st": 0, "prob": 1, "mv": 12_000_000, "tid": "7"}, NOW
    )
    league = LeagueStore(dsn=dsn)
    league.upsert_teams(
        [{"team_id": "7", "name": "Club Seven", "short_name": "SEV", "updated_at": NOW}]
    )
    league.upsert_managers(
        [
            {
                "manager_id": "m2",
                "league_id": "L",
                "name": "Rival",
                "is_self": False,
                "updated_at": NOW,
            }
        ]
    )
    league.write_squads(
        [
            {
                "snapshot_at": NOW,
                "manager_id": "m2",
                "player_id": "a",
                "market_value": 12_000_000,
                "gain_loss": None,
                "on_market": None,
                "source": "ingest",
            }
        ]
    )
    with corpus.connection() as conn:
        conn.execute(
            "INSERT INTO rehoboam.mv_series (player_id, snapshot_at, market_value) VALUES "
            "('a', %s, 10000000), ('a', %s, 11000000)",
            (NOW - 8 * 86400, NOW - 86400 - 60),
        )
    CalibrationStore(dsn=dsn).write_predictions(
        [
            {
                "session_id": "s",
                "player_id": "a",
                "season": "2026/2027",
                "day_number": 4,
                "kickoff": NOW + 86400,
                "predicted_at": NOW - 100,
                "predicted_ep": 61.5,
                "p_status": {1: 0.05, 3: 0.15, 4: 0.1, 5: 0.7},
                "rate": 80.0,
                "prev_status": 5,
                "live_status": 0,
                "position": "Midfielder",
                "team_id": "7",
                "owned": False,
                "listed": False,
                "in_best_11": False,
                "live_ep": None,
                "data_grade": "A",
                "app": "cli",
                "dry_run": True,
                "backfill": False,
            }
        ]
    )
    return league


def test_the_view_has_base_xi_columns_in_order(store_dsn):
    _seed(store_dsn)
    with LeagueStore(dsn=store_dsn).connection() as conn:
        cols = [
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='rehoboam' AND table_name='player_table' ORDER BY ordinal_position"
            ).fetchall()
        ]
    assert cols == [
        "player_id",
        "name",
        "team",
        "position",
        "market_value",
        "trend_24h_pct",
        "trend_7d_pct",
        "points",
        "avg_points",
        "median_points",
        "points_per_million",
        "points_prev",
        "avg_points_prev",
        "appearances",
        "appearances_prev",
        "starts",
        "starts_prev",
        "owner",
        "predicted_ep",
        "p_start",
        "fair_value_gap",
    ]


def test_the_numbers(store_dsn):
    league = _seed(store_dsn)
    rows = {r["player_id"]: r for r in league.player_table()}
    a, b = rows["a"], rows["b"]
    assert a["team"] == "Club Seven" and a["market_value"] == 12_000_000
    assert (
        a["points"] == 100 and a["appearances"] == 2 and a["starts"] == 1
    )  # statuses 5, 3; not 1 or 0
    assert float(a["avg_points"]) == 50.0 and float(a["median_points"]) == 50.0
    assert (
        a["points_prev"] == 100 and a["appearances_prev"] == 1 and a["starts_prev"] == 1
    )
    assert round(float(a["points_per_million"]), 2) == round(100 / 12.0, 2)
    assert round(float(a["trend_24h_pct"]), 1) == round(100 * (12 - 11) / 11, 1)
    assert round(float(a["trend_7d_pct"]), 1) == round(100 * (12 - 10) / 10, 1)
    assert (
        a["owner"] == "Rival"
        and float(a["predicted_ep"]) == 61.5
        and float(a["p_start"]) == 0.7
    )
    assert b["owner"] == "Kickbase" and b["team"] is None and b["points_prev"] is None
    assert b["predicted_ep"] is None and b["trend_24h_pct"] is None


def test_filters_and_order(store_dsn):
    league = _seed(store_dsn)
    assert [r["player_id"] for r in league.player_table(owner="Rival")] == ["a"]
    assert [
        r["player_id"]
        for r in league.player_table(position="Midfielder", order_by="points")
    ] == ["a", "b"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/store/test_player_table.py -q`
Expected: FAIL (`relation "rehoboam.player_table" does not exist` / `AttributeError: player_table`).

- [ ] **Step 3: Write the migration `006_player_table.sql`**

```sql
-- G1: Base XI's player table as a view over the store, plus our three columns.
create or replace view rehoboam.player_table as
with cur as (
    select max(season) as season from rehoboam.player_match_history
),
prev as (
    select max(season) as season from rehoboam.player_match_history
    where season < (select season from cur)
),
mv_now as (
    select distinct on (player_id) player_id, market_value
    from rehoboam.player_status_daily
    where market_value is not null
    order by player_id, day desc
),
hist as (
    select h.player_id,
        sum(h.points) filter (where h.season = c.season and h.status in (3, 5)) as points,
        avg(h.points) filter (where h.season = c.season and h.status in (3, 5)) as avg_points,
        percentile_cont(0.5) within group (order by h.points)
            filter (where h.season = c.season and h.status in (3, 5)) as median_points,
        count(*) filter (where h.season = c.season and h.status in (3, 5)) as appearances,
        count(*) filter (where h.season = c.season and h.status = 5) as starts,
        sum(h.points) filter (where h.season = p.season and h.status in (3, 5)) as points_prev,
        avg(h.points) filter (where h.season = p.season and h.status in (3, 5)) as avg_points_prev,
        count(*) filter (where h.season = p.season and h.status in (3, 5)) as appearances_prev,
        count(*) filter (where h.season = p.season and h.status = 5) as starts_prev
    from rehoboam.player_match_history h
    cross join cur c
    left join prev p on true
    group by h.player_id
),
owner as (
    select s.player_id, m.name
    from rehoboam.manager_squads s
    join rehoboam.managers m on m.manager_id = s.manager_id
    where s.snapshot_at = (select max(snapshot_at) from rehoboam.manager_squads)
),
listed as (
    select player_id from rehoboam.market_listings
    where snapshot_at = (select max(snapshot_at) from rehoboam.market_listings)
),
pred as (
    select distinct on (player_id) player_id, predicted_ep, p_status
    from rehoboam.predictions
    where backfill = false
    order by player_id, predicted_at desc
),
mv_1d as (
    select distinct on (player_id) player_id, market_value
    from rehoboam.mv_series
    where snapshot_at <= extract(epoch from now()) - 86400
    order by player_id, snapshot_at desc
),
mv_7d as (
    select distinct on (player_id) player_id, market_value
    from rehoboam.mv_series
    where snapshot_at <= extract(epoch from now()) - 7 * 86400
    order by player_id, snapshot_at desc
),
base as (
    select u.player_id,
        coalesce(u.last_name, u.player_id) as name,
        t.name as team,
        u.position,
        coalesce(n.market_value, u.market_value) as market_value,
        h.points, h.avg_points, h.median_points, h.appearances, h.starts,
        h.points_prev, h.avg_points_prev, h.appearances_prev, h.starts_prev,
        coalesce(o.name, case when l.player_id is not null then 'market' else 'Kickbase' end) as owner,
        pr.predicted_ep,
        (pr.p_status ->> '5')::double precision as p_start,
        d1.market_value as mv_1d,
        d7.market_value as mv_7d
    from rehoboam.player_universe u
    left join rehoboam.teams t on t.team_id = u.team_id
    left join mv_now n on n.player_id = u.player_id
    left join hist h on h.player_id = u.player_id
    left join owner o on o.player_id = u.player_id
    left join listed l on l.player_id = u.player_id
    left join pred pr on pr.player_id = u.player_id
    left join mv_1d d1 on d1.player_id = u.player_id
    left join mv_7d d7 on d7.player_id = u.player_id
    where u.position is not null
),
fair as (
    select position,
        regr_slope(avg_points, market_value / 1e6) as slope,
        regr_intercept(avg_points, market_value / 1e6) as intercept
    from base
    where avg_points is not null and market_value is not null and market_value > 0
    group by position
)
select b.player_id, b.name, b.team, b.position, b.market_value,
    round((100.0 * (b.market_value - b.mv_1d) / nullif(b.mv_1d, 0))::numeric, 2) as trend_24h_pct,
    round((100.0 * (b.market_value - b.mv_7d) / nullif(b.mv_7d, 0))::numeric, 2) as trend_7d_pct,
    b.points,
    round(b.avg_points::numeric, 1) as avg_points,
    round(b.median_points::numeric, 1) as median_points,
    round((b.points / nullif(b.market_value / 1e6, 0))::numeric, 2) as points_per_million,
    b.points_prev,
    round(b.avg_points_prev::numeric, 1) as avg_points_prev,
    b.appearances, b.appearances_prev, b.starts, b.starts_prev,
    b.owner, b.predicted_ep, b.p_start,
    round((b.avg_points - (f.intercept + f.slope * b.market_value / 1e6))::numeric, 1) as fair_value_gap
from base b
left join fair f on f.position = b.position;
```

`count(*) filter (...)` returns 0 for a player with no rows in that season; the test expects `points_prev is None` for `b` (sum over no rows is NULL) — keep `sum`/`avg` for the None semantics and let counts be 0. Adjust the test's `appearances_prev` expectation for `b` only if you assert on it (the given tests do not).

Add to `LeagueStore`:

```python
    _TABLE_ORDER = {"predicted_ep", "points", "avg_points", "market_value", "points_per_million",
                    "trend_24h_pct", "trend_7d_pct", "fair_value_gap", "name"}

    def player_table(
        self, *, position: str | None = None, owner: str | None = None, order_by: str = "predicted_ep"
    ) -> list[dict[str, Any]]:
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
```

If `tests/store/test_migrate.py::_tables` reads `information_schema.tables` (which lists views), add `player_table` to `EXPECTED_TABLES`; if it filters on `table_type = 'BASE TABLE'`, leave it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/store/test_player_table.py tests/store/test_migrate.py tests/store/test_league_store.py -q`
Expected: PASS. If `percentile_cont ... filter` is rejected by the server version, compute the median as `percentile_cont(0.5) within group (order by case when h.season = c.season and h.status in (3,5) then h.points end)` (NULLs are ignored by ordered-set aggregates).

- [ ] **Step 5: Commit**

```bash
git add rehoboam/store/migrations/006_player_table.sql rehoboam/store/league_store.py tests/store/test_player_table.py tests/store/test_migrate.py
git commit -m "feat(store): the player table view — Base XI's columns plus predicted points, start probability, fair value"
```

______________________________________________________________________

### Task 4: The client remembers the market payload; the trader keeps the squads

**Files:**

- Modify: `rehoboam/kickbase_client.py:262-276` (`get_market`)
- Modify: `rehoboam/api.py:33-38` (`get_market`)
- Modify: `rehoboam/trader.py` (~line 332 market fetch; ~406-460 manager loop; ~786-800 result dict)
- Test: `tests/test_trader_league_state.py` (create)

**Interfaces:**

- Produces: `KickbaseV4Client.last_market_payload: dict | None` (set by `get_market`), `KickbaseAPI.last_market_payload` (mirrors the client's after each `get_market`), `ep_result["market_payload"]: dict | None`, `ep_result["competitor_squads"]: dict[str, list[dict]]` (manager id → raw `it[]`, other managers only), `ep_result["ranking_payload"]: dict | None`.

- [ ] **Step 1: Write the failing test**

```python
"""The session keeps what it fetched so the store can have it without a second call (G1 Task 4)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from rehoboam.config import Settings
from rehoboam.kickbase_client import KickbaseV4Client, MarketPlayer
from rehoboam.trader import Trader

LEAGUE = SimpleNamespace(id="L", name="T")
RAW_MARKET = {
    "it": [
        {
            "i": "11",
            "tid": "7",
            "pos": 3,
            "prc": 1,
            "mv": 1,
            "exs": 100,
            "dt": "2026-09-15T16:05:06Z",
        }
    ]
}
RANKING = {"us": [{"i": "me", "n": "Marco"}, {"i": "m2", "n": "Rival"}], "day": 4}
SQUAD_M2 = {"it": [{"pi": "11", "mv": 1, "mvgl": 0, "iotm": True}]}


def test_client_get_market_keeps_the_raw_payload():
    client = KickbaseV4Client()
    response = SimpleNamespace(status_code=200, json=lambda: RAW_MARKET, text="")
    with patch.object(client.session, "get", return_value=response):
        players = client.get_market("L")
    assert [p.id for p in players] == [
        "11"
    ] and client.last_market_payload == RAW_MARKET


class _Api:
    user = SimpleNamespace(id="me")

    def __init__(self):
        self.client = SimpleNamespace()
        self.last_market_payload = None
        self.calls = []

    def get_market(self, league):
        self.calls.append("market")
        self.last_market_payload = RAW_MARKET
        return [MarketPlayer.from_dict(i) for i in RAW_MARKET["it"]]

    def get_league_ranking(self, league):
        self.calls.append("ranking")
        return RANKING

    def get_manager_squad(self, league, manager_id):
        self.calls.append(f"squad:{manager_id}")
        return SQUAD_M2

    def get_squad(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": 1_000_000, "team_value": 1}

    def get_my_bids(self, league):
        return []

    def get_competition_matchdays(self, competition_id="1"):
        return {}

    def get_starting_eleven(self, league):
        return {"lp": [], "nlp": []}


def test_trader_exposes_market_payload_ranking_and_competitor_squads():
    api = _Api()
    trader = Trader(api, Settings(kickbase_email="t@e.com", kickbase_password="x"))
    # Everything past the fetches is irrelevant here; stub the scoring so the call returns.
    with patch.object(Trader, "_fetch_player_data", return_value=({}, {})):
        result = trader.get_ep_recommendations(LEAGUE)
    assert result["market_payload"] == RAW_MARKET
    assert result["ranking_payload"] == RANKING
    assert result["competitor_squads"] == {"m2": SQUAD_M2["it"]}
    assert api.calls.count("market") == 1 and "squad:me" not in api.calls
```

If `get_ep_recommendations` needs more of the API surface than `_Api` offers (it may call `get_player_details`, `get_player_performance` via `_fetch_player_data`, which is patched, or `get_team_profile`), add the missing no-op methods to `_Api` rather than widening the patch; the assertions that matter are the three result keys and the call counts.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_trader_league_state.py -q`
Expected: FAIL (`last_market_payload` missing / `KeyError: 'market_payload'`).

- [ ] **Step 3: Implement**

`kickbase_client.py`: in `KickbaseV4Client.__init__` add `self.last_market_payload: dict | None = None`; in `get_market`, after `data = response.json()`, add `self.last_market_payload = data`.

`api.py`: in `KickbaseAPI.__init__` add `self.last_market_payload: dict | None = None`; in `get_market` after the client call: `self.last_market_payload = getattr(self.client, "last_market_payload", None)` (before returning).

`trader.py`:

- after `market_players_list = self.api.get_market(league)`: `market_payload = getattr(self.api, "last_market_payload", None)`.

- in the ranking block: `ranking_payload: dict | None = None` and `competitor_squads: dict[str, list] = {}` initialised before the `try` next to `competitor_player_ids`; after `ranking = self.api.get_league_ranking(league)` set `ranking_payload = ranking`; inside the per-manager `try` after `mgr_squad = self.api.get_manager_squad(...)` add `competitor_squads[str(mgr_id)] = list(mgr_squad.get("it", []) or [])`.

- result dict: add `"market_payload": market_payload, "ranking_payload": ranking_payload, "competitor_squads": competitor_squads`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_trader_league_state.py tests/test_approval_webhook.py tests/test_approve_all_batch.py tests/test_session_facts_wiring.py -q`
Expected: PASS (fakes without `last_market_payload` yield `None`, which is fine).

- [ ] **Step 5: Commit**

```bash
git add rehoboam/kickbase_client.py rehoboam/api.py rehoboam/trader.py tests/test_trader_league_state.py
git commit -m "feat(trader): keep the market payload, the ranking and the competitor squads the session fetched"
```

______________________________________________________________________

### Task 5: The session writes the league state

**Files:**

- Modify: `rehoboam/auto_trader.py` (constructor: `league_store=None`; step 2a: `_write_league_state(ctx, league)`)
- Modify: `tests/test_session_facts_wiring.py` (append)

**Interfaces:**

- Consumes: Task 1 `LeagueStore`, Task 2 builders, Task 4 result keys.

- Produces: `AutoTrader(..., league_store: LeagueStore | None = None)`, `AutoTrader._write_league_state(ctx, league) -> dict[str, int]` (counts: `listings`, `managers`, `squads`), recorded in `self._facts.extra["league_state"]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_session_facts_wiring.py`)

```python
from rehoboam.store.league_store import LeagueStore  # noqa: E402

RAW_MARKET = {
    "it": [
        {
            "i": "lst",
            "tid": "8",
            "pos": 4,
            "prc": 3_000_000,
            "mv": 2_900_000,
            "exs": 500,
            "dt": "2026-09-15T16:05:06Z",
            "u": {"i": "m2"},
        }
    ]
}
RANKING = {"us": [{"i": "3616202", "n": "Marco"}, {"i": "m2", "n": "Rival"}]}


def test_a_session_writes_listings_managers_and_squads(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad())
    ctx = ctx_factory()
    ctx.ep_result.update(
        {
            "market_payload": RAW_MARKET,
            "ranking_payload": RANKING,
            "competitor_squads": {
                "m2": [{"pi": "r1", "mv": 5, "mvgl": 1, "iotm": False}]
            },
        }
    )
    with patch.object(AutoTrader, "_build_session_context", return_value=ctx):
        session = trader.run_full_session(LEAGUE)
    league = LeagueStore(dsn=store_dsn)
    assert [r["player_id"] for r in league.latest_market()] == ["lst"]
    assert (
        league.latest_market()[0]["seller_id"] == "m2"
        and league.latest_market()[0]["source"] == "session"
    )
    owners = league.owner_of(["r1", "gk", "lst"])
    assert owners == {"r1": "Rival", "gk": "Marco", "lst": "market"}
    facts = SessionStore(dsn=store_dsn).facts(session.session_id)
    assert facts["extra"]["league_state"] == {
        "listings": 1,
        "managers": 2,
        "squads": 12,
    }


def test_missing_payloads_write_nothing_and_do_not_fail(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad())
    with patch.object(AutoTrader, "_build_session_context", return_value=ctx_factory()):
        session = trader.run_full_session(LEAGUE)
    assert LeagueStore(dsn=store_dsn).latest_market() == []
    facts = SessionStore(dsn=store_dsn).facts(session.session_id)
    assert facts["errors"] == 0
```

`_trader`'s `_Api` has `user = SimpleNamespace(id="3616202")`, so our own eleven-player squad is written under that id plus the one rival player: 12 squad rows. If `SessionFacts.extra` is `None` at that point, the method creates the dict.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_session_facts_wiring.py -q -k league`
Expected: FAIL.

- [ ] **Step 3: Implement in `rehoboam/auto_trader.py`**

Constructor (next to `calibration_store`): keyword `league_store=None`; `self._league_store = league_store or LeagueStore()` (import next to `CalibrationStore`).

Step 2a, right after the league-predictions block:

```python
try:
    counts = self._write_league_state(ctx, league)
    extra = dict(self._facts.extra or {})
    extra["league_state"] = counts
    self._facts.extra = extra
except Exception:
    logger.exception("league state write failed (non-fatal)")
```

The method:

```python
def _write_league_state(self, ctx: EPSessionContext, league) -> dict[str, int]:
    """G1: persist the market, the managers and every squad the session already fetched.

    No API call here: `Trader` keeps the raw market payload, the ranking and
    the competitor squads on `ep_result`; our own squad is `ctx.squad`.
    """
    from rehoboam.enrichment.rows import (
        manager_rows,
        manager_squad_rows,
        market_listing_rows,
        own_squad_rows,
    )

    snapshot_at = time.time()
    my_id = str(getattr(getattr(self.api, "user", None), "id", "") or "")
    store = self._league_store
    counts = {"listings": 0, "managers": 0, "squads": 0}

    ranking = ctx.ep_result.get("ranking_payload")
    if ranking:
        counts["managers"] = store.upsert_managers(
            manager_rows(
                ranking,
                league_id=str(league.id),
                our_user_id=my_id,
                updated_at=snapshot_at,
            )
        )
    market = ctx.ep_result.get("market_payload")
    if market:
        counts["listings"] = store.write_listings(
            market_listing_rows(
                market, snapshot_at=snapshot_at, our_user_id=my_id, source="session"
            )
        )
    squad_rows: list[dict] = []
    for mgr_id, items in (ctx.ep_result.get("competitor_squads") or {}).items():
        squad_rows += manager_squad_rows(
            str(mgr_id), items, snapshot_at=snapshot_at, source="session"
        )
    if my_id and ctx.squad:
        squad_rows += own_squad_rows(
            my_id, ctx.squad, snapshot_at=snapshot_at, source="session"
        )
    if squad_rows:
        counts["squads"] = store.write_squads(squad_rows)
    logger.info("league-state listings=%d managers=%d squads=%d", *counts.values())
    return counts
```

`manager_squads` rows need a `managers` row for `owner_of` to name them; the session writes managers first, and the own squad is written even when the ranking was missing (the name then comes from a later run's ranking).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_session_facts_wiring.py tests/test_trading_mode.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/auto_trader.py tests/test_session_facts_wiring.py
git commit -m "feat(session): the market, the managers and every squad are written from what the session fetched"
```

______________________________________________________________________

### Task 6: The ingestion refresh and the transfers kind

**Files:**

- Create: `rehoboam/enrichment/league_refresh.py`
- Modify: `rehoboam/enrichment/ingest.py` (`IngestStats` gains `league: dict | None`; `run_ingestion(..., league_store=None, learner=None, our_user_id=None, season=None, now=None)` calls the refresh before the loop; the `transfers` kind joins the loop)
- Modify: `rehoboam/store/corpus_store.py:315-372` (`players_needing_any_refresh` accepts `"transfers"`)
- Modify: `rehoboam/config.py` (`ingest_transfers_stale_after_hours: float = 168.0`)
- Modify: `deploy/azure_function_external/function_app.py`, `rehoboam/cli.py` `ingest_cmd` (pass the new arguments)
- Create: `tests/test_enrichment/test_league_refresh.py`; modify `tests/test_enrichment/test_ingest.py` (one test: transfers kind scheduled)

**Interfaces:**

- Produces: `LeagueRefreshStats` dataclass (`listings, managers, squads, transfers, fixtures, table_rows, teams, requests, failed`), `run_league_refresh(client, league_store, learner, *, league_id, our_user_id, season, now, teams_stale_after_s=7*86400) -> LeagueRefreshStats`.

- `client` is the ingest's counting client (`api.client` wrapped by `_counting_client`): methods `get_market(league_id)` (list; the raw payload is `client.last_market_payload` — the counting wrapper must pass attribute reads through: extend `_counting_client` so `__getattr__` returns non-callables as-is), `get_league_ranking(league_id)`, `get_manager_squad(league_id, mid)`, `get_manager_transfer_history(league_id, mid, start=0)`, `get_competition_matchdays()`, `get_competition_table()`, `get_team_profile(league_id, tid)`.

- [ ] **Step 1: Write the failing tests**

```python
"""One league refresh per ingest run: market, managers, squads, page-0 transfers, fixtures, table, clubs."""

from __future__ import annotations

from unittest.mock import MagicMock

from rehoboam.bid_learner import BidLearner
from rehoboam.enrichment.league_refresh import run_league_refresh
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore

NOW = 1_789_600_000.0
MARKET = {
    "it": [
        {
            "i": "11",
            "tid": "7",
            "pos": 2,
            "prc": 1,
            "mv": 1,
            "exs": 10,
            "dt": "2026-09-15T16:05:06Z",
        }
    ]
}
RANKING = {"us": [{"i": "me", "n": "Marco"}, {"i": "m2", "n": "Rival"}], "day": 4}
SCHEDULE = {
    "it": [
        {
            "day": 4,
            "it": [
                {"mi": "1", "dt": "2026-09-19T13:30:00Z", "st": 0, "t1": "7", "t2": "8"}
            ],
        }
    ]
}
TABLE = {
    "it": [
        {"tid": "7", "cpl": 1, "cp": 9, "mc": 3, "gd": 5},
        {"tid": "8", "cpl": 2, "cp": 6, "mc": 3, "gd": 0},
    ]
}


def _client(fail_squad_for=None):
    c = MagicMock()
    c.last_market_payload = None

    def get_market(league_id):
        c.last_market_payload = MARKET
        return []

    c.get_market.side_effect = get_market
    c.get_league_ranking.return_value = RANKING

    def squad(league_id, mid):
        if mid == fail_squad_for:
            raise RuntimeError("boom")
        return {"it": [{"pi": f"p-{mid}", "mv": 1}]}

    c.get_manager_squad.side_effect = squad
    c.get_manager_transfer_history.return_value = {
        "it": [
            {
                "pi": "11",
                "pn": "X",
                "tty": 1,
                "trp": 1_000,
                "dt": "2026-09-14T10:00:00Z",
            }
        ]
    }
    c.get_competition_matchdays.return_value = SCHEDULE
    c.get_competition_table.return_value = TABLE
    c.get_team_profile.side_effect = lambda league_id, tid: {
        "tid": tid,
        "tn": f"Club {tid}",
        "ts": tid,
    }
    return c


def _seed_universe(dsn):
    CorpusStore(dsn=dsn).upsert_players(
        [
            {
                "player_id": "11",
                "first_name": None,
                "last_name": "A",
                "position": "Defender",
                "team_id": "7",
                "market_value": 1,
                "average_points": 1.0,
            },
            {
                "player_id": "12",
                "first_name": None,
                "last_name": "B",
                "position": "Defender",
                "team_id": "8",
                "market_value": 1,
                "average_points": 1.0,
            },
        ]
    )


def test_a_refresh_writes_everything_and_counts_requests(store_dsn):
    _seed_universe(store_dsn)
    league, learner, client = (
        LeagueStore(dsn=store_dsn),
        BidLearner(dsn=store_dsn),
        _client(),
    )
    stats = run_league_refresh(
        client,
        league,
        learner,
        league_id="L",
        our_user_id="me",
        season="2026/2027",
        now=NOW,
    )
    assert (stats.listings, stats.managers, stats.squads, stats.transfers) == (
        1,
        2,
        2,
        2,
    )
    assert (stats.fixtures, stats.table_rows, stats.teams, stats.failed) == (1, 2, 2, 0)
    # market 1 + ranking 1 + squads 2 + transfers 2 + schedule 1 + table 1 + teams 2
    assert stats.requests == 10
    assert league.owner_of(["p-m2", "p-me", "11"]) == {
        "p-m2": "Rival",
        "p-me": "Marco",
        "11": "market",
    }
    with league.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM rehoboam.manager_transfers"
            ).fetchone()["n"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT name FROM rehoboam.teams WHERE team_id='7'"
            ).fetchone()["name"]
            == "Club 7"
        )
        assert (
            conn.execute(
                "SELECT day_number FROM rehoboam.league_table LIMIT 1"
            ).fetchone()["day_number"]
            == 4
        )


def test_a_failing_manager_call_is_counted_and_the_rest_proceeds(store_dsn):
    _seed_universe(store_dsn)
    league, learner = LeagueStore(dsn=store_dsn), BidLearner(dsn=store_dsn)
    stats = run_league_refresh(
        _client(fail_squad_for="m2"),
        league,
        learner,
        league_id="L",
        our_user_id="me",
        season="2026/2027",
        now=NOW,
    )
    assert stats.failed == 1 and stats.squads == 1 and stats.fixtures == 1


def test_teams_are_refreshed_weekly_only(store_dsn):
    _seed_universe(store_dsn)
    league, learner = LeagueStore(dsn=store_dsn), BidLearner(dsn=store_dsn)
    run_league_refresh(
        _client(),
        league,
        learner,
        league_id="L",
        our_user_id="me",
        season="2026/2027",
        now=NOW,
    )
    again = run_league_refresh(
        _client(),
        league,
        learner,
        league_id="L",
        our_user_id="me",
        season="2026/2027",
        now=NOW + 3600,
    )
    assert again.teams == 0 and again.requests == 8
```

Append to `tests/test_enrichment/test_ingest.py` a test that `players_needing_any_refresh({"transfers": now})` lists a player never fetched for transfers, and that `run_ingestion` with `transfers_stale_after_seconds` given calls `get_player_transfer_history` once per player and `mark_fetched(..., transfers=True)` (follow the file's existing fake-client pattern).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_enrichment/test_league_refresh.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `rehoboam/enrichment/league_refresh.py`**

```python
"""One league-state refresh per ingest run (spec G1): ~35 requests before the per-player loop.

Every fetch is its own try/except: a manager whose squad call fails costs one
`failed` and nothing else. Requests are counted here as well so the caller can
add them to the run's totals (the client passed in is already budget-counting).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rehoboam.enrichment.rows import (
    fixture_rows,
    league_table_rows,
    manager_rows,
    manager_squad_rows,
    market_listing_rows,
    team_row,
)

logger = logging.getLogger(__name__)

TEAMS_STALE_AFTER_S = 7 * 86400


@dataclass
class LeagueRefreshStats:
    listings: int = 0
    managers: int = 0
    squads: int = 0
    transfers: int = 0
    fixtures: int = 0
    table_rows: int = 0
    teams: int = 0
    requests: int = 0
    failed: int = 0


def run_league_refresh(
    client,
    league_store,
    learner,
    *,
    league_id: str,
    our_user_id: str,
    season: str,
    now: float,
    teams_stale_after_s: float = TEAMS_STALE_AFTER_S,
) -> LeagueRefreshStats:
    stats = LeagueRefreshStats()

    def attempt(what, fn):
        stats.requests += 1
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            stats.failed += 1
            logger.warning("league-refresh %s failed: %s", what, e)
            return None

    # Market: the client keeps the raw payload; the parsed list is not needed here.
    if attempt("market", lambda: client.get_market(league_id)) is not None:
        payload = getattr(client, "last_market_payload", None)
        if payload:
            stats.listings = league_store.write_listings(
                market_listing_rows(
                    payload, snapshot_at=now, our_user_id=our_user_id, source="ingest"
                )
            )

    ranking = attempt("ranking", lambda: client.get_league_ranking(league_id))
    managers = manager_rows(
        ranking or {}, league_id=league_id, our_user_id=our_user_id, updated_at=now
    )
    if managers:
        stats.managers = league_store.upsert_managers(managers)

    squad_rows: list[dict] = []
    transfer_rows: list[dict] = []
    for m in managers:
        mid = m["manager_id"]
        squad = attempt(
            f"squad {mid}", lambda mid=mid: client.get_manager_squad(league_id, mid)
        )
        if squad is not None:
            squad_rows += manager_squad_rows(
                mid, (squad or {}).get("it") or [], snapshot_at=now, source="ingest"
            )
        history = attempt(
            f"transfers {mid}",
            lambda mid=mid: client.get_manager_transfer_history(league_id, mid),
        )
        for t in (history or {}).get("it") or []:
            pid, tdt = t.get("pi"), t.get("dt")
            if not pid or not tdt:
                continue
            transfer_rows.append(
                {
                    "league_id": league_id,
                    "manager_id": mid,
                    "transfer_dt": tdt,
                    "player_id": str(pid),
                    "player_name": t.get("pn", ""),
                    "transfer_type": t.get("tty"),
                    "transfer_price": t.get("trp"),
                }
            )
    if squad_rows:
        stats.squads = league_store.write_squads(squad_rows)
    if transfer_rows and learner is not None:
        stats.transfers = int(
            learner.record_manager_transfers(transfer_rows) or len(transfer_rows)
        )

    schedule = attempt("schedule", lambda: client.get_competition_matchdays())
    if schedule:
        stats.fixtures = league_store.upsert_fixtures(
            fixture_rows(schedule, season=season, updated_at=now)
        )

    table = attempt("table", lambda: client.get_competition_table())
    if table:
        day_number = int((ranking or {}).get("day") or 0)
        stats.table_rows = league_store.write_table(
            league_table_rows(
                table, season=season, day_number=day_number, updated_at=now
            )
        )

    for tid in league_store.teams_older_than(now - teams_stale_after_s):
        profile = attempt(
            f"team {tid}", lambda tid=tid: client.get_team_profile(league_id, tid)
        )
        row = team_row(profile or {}, updated_at=now)
        if row:
            stats.teams += league_store.upsert_teams([row])

    logger.info(
        "league-refresh listings=%d managers=%d squads=%d transfers=%d fixtures=%d table=%d teams=%d "
        "requests=%d failed=%d",
        stats.listings,
        stats.managers,
        stats.squads,
        stats.transfers,
        stats.fixtures,
        stats.table_rows,
        stats.teams,
        stats.requests,
        stats.failed,
    )
    return stats
```

`BidLearner.record_manager_transfers(rows)` returns the number of rows upserted (check `bid_learner.py:967`); the `or len(...)` guards a `None` return. `teams_older_than` is the Task 1 reader.

**`ingest.py` changes.** `IngestStats` gains `league: dict | None = None` and `transfers_fetched: int = 0`. `_counting_client.__getattr__` returns non-callable attributes directly (so `last_market_payload` passes through) and only wraps callables. `run_ingestion` gains keyword arguments `league_store=None, learner=None, our_user_id: str | None = None, season: str | None = None, transfers_stale_after_seconds: float | None = None`; inside `with store.session():` right after `store.upsert_players(rows)`:

```python
if league_store is not None and season:
    league = run_league_refresh(
        api,
        league_store,
        learner,
        league_id=league_id,
        our_user_id=our_user_id or "",
        season=season,
        now=budget.now(),
    )
    stats.league = asdict(league)
```

(`BudgetExhausted` raised by the counting client inside `attempt` is caught there as a failure — wrap `attempt` so `BudgetExhausted` re-raises: `except BudgetExhausted: raise` before the generic except, importing it from `rehoboam.enrichment.ingest` lazily or moving `BudgetExhausted` to a tiny `enrichment/budget.py` both modules import — prefer the move, and keep `BudgetExhausted` re-exported from `ingest.py` so existing imports and tests still work.) Add the `"transfers"` window and fetcher when `transfers_stale_after_seconds` is given:

```python
if transfers_stale_after_seconds is not None:
    windows["transfers"] = now - transfers_stale_after_seconds
    fetchers["transfers"] = (
        lambda pid: api.get_player_transfer_history(league_id=league_id, player_id=pid),
        lambda pid, h: store.record_player_transfers(pid, h),
        "transfers_fetched",
    )
```

`players_needing_any_refresh`: the kinds tuple becomes `("status", "performance", "mv", "transfers")`. `config.py`: `ingest_transfers_stale_after_hours: float = Field(default=168.0, description="Per-player transfer history refresh window (weekly).")`. `function_app.py` and `cli.py`'s `ingest_cmd` pass `league_store=LeagueStore()`, `learner=BidLearner()`, `our_user_id=str(api.user.id)`, `season=calibration_store.current_season()` (the handler already has it; the CLI reads it the same way), `transfers_stale_after_seconds=settings.ingest_transfers_stale_after_hours * 3600.0`. Check `get_player_transfer_history`'s exact keyword names in `kickbase_client.py:852`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_enrichment/ tests/test_cli_ingest.py tests/store/test_corpus_store.py tests/test_calibrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/enrichment/league_refresh.py rehoboam/enrichment/ingest.py rehoboam/enrichment/budget.py rehoboam/store/corpus_store.py rehoboam/config.py deploy/azure_function_external/function_app.py rehoboam/cli.py tests/test_enrichment/
git commit -m "feat(ingest): one league-state refresh per run; per-player transfers weekly"
```

______________________________________________________________________

### Task 7: `rehoboam players`, `rehoboam market`, `rehoboam backfill-league`

**Files:**

- Modify: `rehoboam/cli.py` (three commands after `calibrate_cmd`)

- Test: `tests/store/test_cli.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_players_prints_the_view(store_dsn):
    from tests.store.test_player_table import _seed

    _seed(store_dsn)
    result = runner.invoke(app, ["players", "--position", "Midfielder"])
    assert result.exit_code == 0, result.output
    assert (
        "Alpha" in result.output
        and "Rival" in result.output
        and "Club Seven" in result.output
    )


def test_market_prints_the_newest_snapshot(store_dsn):
    from rehoboam.store.league_store import LeagueStore

    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": 1.0,
                "player_id": "a",
                "ask": 5_000_000,
                "market_value": 4_900_000,
                "mv_trend": 1,
                "seller_id": None,
                "offer_count": 2,
                "our_bid": None,
                "listed_at": None,
                "expires_at": 3601.0,
                "status": 0,
                "lineup_probability": 1,
                "source": "ingest",
            }
        ]
    )
    result = runner.invoke(app, ["market"])
    assert result.exit_code == 0, result.output
    assert "5,000,000" in result.output and "Kickbase" in result.output


def test_backfill_league_walks_every_page(store_dsn, monkeypatch):
    from unittest.mock import patch

    pages = {
        0: {
            "it": [
                {
                    "pi": str(i),
                    "pn": "x",
                    "tty": 1,
                    "trp": 1,
                    "dt": f"2026-08-{10 + i:02d}T10:00:00Z",
                }
                for i in range(25)
            ]
        },
        25: {
            "it": [
                {
                    "pi": "99",
                    "pn": "y",
                    "tty": 2,
                    "trp": 2,
                    "dt": "2026-08-01T10:00:00Z",
                }
            ]
        },
    }
    api = type(
        "Api",
        (),
        {
            "user": type("U", (), {"id": "me"})(),
            "get_league_ranking": lambda self, league: {
                "us": [{"i": "me", "n": "Marco"}, {"i": "m2", "n": "Rival"}]
            },
            "get_manager_transfer_history": lambda self, league, mid, start=0: pages.get(
                start, {"it": []}
            ),
        },
    )()
    league = type("L", (), {"id": "L"})()
    with patch("rehoboam.cli._login_and_get_league", return_value=(api, None, league)):
        result = runner.invoke(app, ["backfill-league"])
    assert result.exit_code == 0, result.output
    with connect(store_dsn) as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM rehoboam.manager_transfers"
        ).fetchone()["n"]
    assert n == 52  # 26 per manager × 2 managers
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/store/test_cli.py -q -k "players or market or backfill_league"`
Expected: FAIL (`No such command`).

- [ ] **Step 3: Implement in `rehoboam/cli.py`**

```python
@app.command("players")
def players_cmd(
    position: str | None = typer.Option(
        None, "--position", help="Goalkeeper|Defender|Midfielder|Forward"
    ),
    owner: str | None = typer.Option(
        None, "--owner", help="Manager name, 'market' or 'Kickbase'."
    ),
    sort: str = typer.Option(
        "predicted_ep", "--sort", help="Column to sort by (descending)."
    ),
    limit: int = typer.Option(60, "--limit"),
):
    """Base XI's player table from the store, with our predicted points beside it."""
    from .store.league_store import LeagueStore

    _ensure_store()
    rows = LeagueStore().player_table(position=position, owner=owner, order_by=sort)[
        :limit
    ]
    table = Table(title=f"players ({len(rows)})")
    cols = [
        ("name", "Name"),
        ("team", "Team"),
        ("position", "Pos"),
        ("market_value", "MW"),
        ("trend_24h_pct", "24h%"),
        ("trend_7d_pct", "7d%"),
        ("points", "Pts"),
        ("avg_points", "Ø"),
        ("median_points", "Med"),
        ("points_per_million", "Pts/M"),
        ("points_prev", "Pts-1"),
        ("avg_points_prev", "Ø-1"),
        ("appearances", "Eins"),
        ("appearances_prev", "Eins-1"),
        ("starts", "S11"),
        ("starts_prev", "S11-1"),
        ("owner", "Besitzer"),
        ("predicted_ep", "EP"),
        ("p_start", "P(start)"),
        ("fair_value_gap", "Fair"),
    ]
    for _key, label in cols:
        table.add_column(
            label,
            justify=(
                "right" if label not in ("Name", "Team", "Pos", "Besitzer") else "left"
            ),
        )
    for r in rows:
        table.add_row(*[_cell(r[key]) for key, _label in cols])
    console.print(table)


def _cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}" if abs(value) < 100 else f"{value:,.0f}"
    if isinstance(value, int) and abs(value) >= 10_000:
        return f"{value:,}"
    return str(value)


@app.command("market")
def market_cmd():
    """The newest market snapshot with our prediction per listing and the time to expiry."""
    import time

    from .store.calibration_store import CalibrationStore
    from .store.league_store import LeagueStore

    _ensure_store()
    league = LeagueStore()
    listings = league.latest_market()
    if not listings:
        console.print("[yellow]no market snapshot in the store yet[/yellow]")
        return
    owners = league.owner_of([r["player_id"] for r in listings])
    names = {
        r["player_id"]: (r["name"], r["team"], r["position"], r["predicted_ep"])
        for r in league.player_table()
    }
    now = time.time()
    table = Table(
        title=f"market snapshot {time.strftime('%Y-%m-%d %H:%M', time.gmtime(listings[0]['snapshot_at']))} UTC"
    )
    for label in (
        "Name",
        "Team",
        "Pos",
        "Ask",
        "MW",
        "Seller",
        "Offers",
        "Expires in",
        "EP",
    ):
        table.add_column(
            label,
            justify="left" if label in ("Name", "Team", "Pos", "Seller") else "right",
        )
    for r in listings:
        name, team, pos, ep = names.get(
            r["player_id"], (r["player_id"], None, None, None)
        )
        seller = owners.get(r["player_id"]) if r["seller_id"] else "Kickbase"
        expires = f"{(r['expires_at'] - now) / 3600:.1f} h" if r["expires_at"] else "—"
        table.add_row(
            str(name),
            _cell(team),
            _cell(pos),
            f"{r['ask']:,}",
            _cell(r["market_value"]),
            str(seller or r["seller_id"]),
            _cell(r["offer_count"]),
            expires,
            _cell(ep),
        )
    console.print(table)


@app.command("backfill-league")
def backfill_league_cmd(
    league_index: int = typer.Option(0, "--league", "-l"),
    max_pages: int = typer.Option(
        40, "--max-pages", help="Safety cap per manager (25 transfers each)."
    ),
):
    """Every manager's full transfer history into manager_transfers (once; resumable by re-running)."""
    import time

    from .bid_learner import BidLearner
    from .enrichment.rows import manager_rows
    from .store.league_store import LeagueStore

    _ensure_store()
    api, _settings, league = _login_and_get_league(league_index)
    ranking = api.get_league_ranking(league)
    managers = manager_rows(
        ranking,
        league_id=str(league.id),
        our_user_id=str(api.user.id),
        updated_at=time.time(),
    )
    LeagueStore().upsert_managers(managers)
    learner = BidLearner()
    total = 0
    for m in managers:
        start = 0
        for _ in range(max_pages):
            page = api.get_manager_transfer_history(
                league, m["manager_id"], start=start
            )
            items = (page or {}).get("it") or []
            rows = [
                {
                    "league_id": str(league.id),
                    "manager_id": m["manager_id"],
                    "transfer_dt": t["dt"],
                    "player_id": str(t["pi"]),
                    "player_name": t.get("pn", ""),
                    "transfer_type": t.get("tty"),
                    "transfer_price": t.get("trp"),
                }
                for t in items
                if t.get("pi") and t.get("dt")
            ]
            if rows:
                learner.record_manager_transfers(rows)
                total += len(rows)
            if len(items) < 25:
                break
            start += 25
        console.print(f"{m['name']}: history read")
    console.print(f"manager_transfers: {total} rows written")
```

`KickbaseAPI.get_manager_transfer_history(league, manager_id, start=0)` (`api.py:187`) is the wrapper the backfill calls; `Table` and `console` are already imported in `cli.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/store/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/cli.py tests/store/test_cli.py
git commit -m "feat(cli): players, market and backfill-league"
```

______________________________________________________________________

### Task 8: Docs and checks

**Files:**

- Modify: `CLAUDE.md` (Common Commands: `players`, `market`, `backfill-league`; store section bullet "**League state (G1, 2026-09-16)**" naming the six tables, the view, the no-new-session-call rule and the ~35-request refresh; Current state list one line)

- Modify: `docs/superpowers/specs/2026-09-16-complete-database-design.md` (G1 heading gets "shipped 2026-09-16")

- [ ] **Step 1: Edit the docs as above.**

- [ ] **Step 2: Run** `.venv/bin/pytest -q` (count), `.venv/bin/ruff check rehoboam/ tests/ deploy/`, `.venv/bin/bandit -r rehoboam/ -c pyproject.toml` (severity counts), `.venv/bin/black --check` on every file the branch touched. Report pre-existing findings as such.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-16-complete-database-design.md
git commit -m "docs: league state tables, the player table view, the new commands"
```

______________________________________________________________________

## Controller-run steps after the plan

1. Apply migrations 005 and 006 to prod as admin (through `store.migrate.migrate` with the Settings admin DSN, as for 004).
1. `rehoboam backfill-league` against prod (14 managers × ~13 pages ≈ 180 requests).
1. Live dry-run `status`: `market_listings` rows = market size, `manager_squads` ≈ 14 squads, `session_facts.extra.league_state` counts.
1. `rehoboam players` and `rehoboam market`; spot-check three players against Base XI.
1. Open the PR, merge, CI deploys; verify the next ingest run's `extra.league` counts (~35 requests) and the 17:00/20:00 rows.
