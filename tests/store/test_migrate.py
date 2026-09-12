"""Migrations create the schema exactly once and are idempotent."""

from __future__ import annotations

from rehoboam.store import SCHEMA, connect
from rehoboam.store.migrate import applied_versions, migrate

EXPECTED_TABLES = {
    "schema_migrations",
    # learning DB
    "auction_outcomes",
    "flip_outcomes",
    "matchday_outcomes",
    "pending_bids",
    "pending_bid_sell_plans",
    "tracked_purchases",
    "recently_sold",
    "predicted_eps",
    "team_value_history",
    "player_mv_history",
    "matchday_lineup_results",
    "league_rank_history",
    "manager_profile_history",
    "buy_decisions",
    "manager_transfers",
    "forced_sales",
    "trade_proposals",
    "league_transfers",
    "market_value_snapshots",
    # corpus
    "player_universe",
    "player_match_history",
    "mv_series",
    "sweep_progress",
    "player_transfers",
    # cache
    "api_cache",
}


def _tables(conn) -> set[str]:
    rows = conn.execute(
        "select table_name from information_schema.tables where table_schema = %s",
        (SCHEMA,),
    ).fetchall()
    return {r["table_name"] for r in rows}


def test_migrate_creates_every_table_in_the_rehoboam_schema(store_dsn):
    with connect(store_dsn) as conn:
        applied = migrate(conn)
        assert applied == ["001_schema.sql"]
        assert _tables(conn) == EXPECTED_TABLES
        public = conn.execute(
            "select count(*) as n from information_schema.tables where table_schema = 'public'"
        ).fetchone()["n"]
        assert public == 0, "public must stay empty"


def test_migrate_is_idempotent(store_dsn):
    with connect(store_dsn) as conn:
        migrate(conn)
        assert migrate(conn) == []
        assert applied_versions(conn) == {1}


def test_identity_columns_accept_explicit_ids_and_continue_after_them(store_dsn):
    with connect(store_dsn) as conn:
        migrate(conn)
        conn.execute(
            f"insert into {SCHEMA}.buy_decisions (id, timestamp, player_id, decision, reason) "
            "values (41, 1.0, 'p', 'skip', 'test')"
        )
        conn.execute(
            f"select setval(pg_get_serial_sequence('{SCHEMA}.buy_decisions', 'id'), "
            f"(select max(id) from {SCHEMA}.buy_decisions))"
        )
        conn.execute(
            f"insert into {SCHEMA}.buy_decisions (timestamp, player_id, decision, reason) "
            "values (2.0, 'q', 'buy', 'test')"
        )
        ids = [
            r["id"]
            for r in conn.execute(f"select id from {SCHEMA}.buy_decisions order by id").fetchall()
        ]
    assert ids == [41, 42]


def test_flip_outcomes_keeps_its_unique_player_buy_date(store_dsn):
    with connect(store_dsn) as conn:
        migrate(conn)
        row = (
            "insert into rehoboam.flip_outcomes (player_id, player_name, buy_price, sell_price, "
            "profit, profit_pct, hold_days, buy_date, sell_date) "
            "values ('p', 'P', 10, 12, 2, 20.0, 3, 100.0, 400.0) on conflict do nothing"
        )
        conn.execute(row)
        conn.execute(row)
        n = conn.execute("select count(*) as n from rehoboam.flip_outcomes").fetchone()["n"]
    assert n == 1
