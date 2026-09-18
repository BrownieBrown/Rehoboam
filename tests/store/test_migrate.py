"""Migrations create the schema exactly once and are idempotent."""

from __future__ import annotations

import psycopg
import pytest

from rehoboam.store import SCHEMA, connect
from rehoboam.store.bootstrap import ROLE, bootstrap
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
    "player_status_daily",
    # cache
    "api_cache",
    # session facts (PR D)
    "session_facts",
    "integrity_failures",
    # calibration (PR E)
    "predictions",
    "calibration_rows",
    "calibration_reports",
    # league state (G1)
    "market_listings",
    "managers",
    "manager_squads",
    "fixtures",
    "league_table",
    "teams",
    "player_table",
    # dashboard views (task 1)
    "web_players",
    "web_squad",
    "web_session_summary",
    "web_market",
    "web_ownership",
    "web_calibration",
    # market-value forecast (008)
    "mv_forecasts",
    "web_mv_forecast",
    "web_mv_accuracy",
    # player detail overlay (013)
    "web_player_seasons",
    "web_player_matches",
    "web_player_mv",
}


def _tables(conn) -> set[str]:
    rows = conn.execute(
        "select table_name from information_schema.tables where table_schema = %s",
        (SCHEMA,),
    ).fetchall()
    return {r["table_name"] for r in rows}


def test_migrate_creates_every_table_in_the_rehoboam_schema(blank_dsn):
    with connect(blank_dsn) as conn:
        applied = migrate(conn)
        assert applied == [
            "001_schema.sql",
            "002_player_status_daily.sql",
            "003_session_facts.sql",
            "004_calibration.sql",
            "005_league_state.sql",
            "006_player_table.sql",
            "007_web_views.sql",
            "008_mv_forecast.sql",
            "009_fair_price.sql",
            "010_market_trend_ppm.sql",
            "011_trend_from_last_change.sql",
            "012_fair_price_min_apps.sql",
            "013_player_detail.sql",
        ]
        assert _tables(conn) == EXPECTED_TABLES
        public = conn.execute(
            "select count(*) as n from information_schema.tables where table_schema = 'public'"
        ).fetchone()["n"]
        assert public == 0, "public must stay empty"


def test_migrate_is_idempotent(store_dsn):
    with connect(store_dsn) as conn:
        migrate(conn)
        assert migrate(conn) == []
        assert applied_versions(conn) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}


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


def test_alter_added_columns_are_in_the_schema(store_dsn):
    with connect(store_dsn) as conn:
        migrate(conn)
        rows = conn.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = %s",
            (SCHEMA,),
        ).fetchall()
        pairs = {(r["table_name"], r["column_name"]) for r in rows}
    assert pairs >= {
        ("flip_outcomes", "trend_pct_at_buy"),
        ("flip_outcomes", "mv_at_buy"),
        ("flip_outcomes", "pct_below_peak_30d_at_buy"),
        ("pending_bids", "tier"),
        ("trade_proposals", "tier"),
        ("trade_proposals", "auto_approve_at"),
        ("trade_proposals", "batch_id"),
    }


def test_a_failing_migration_leaves_earlier_ones_applied(blank_dsn, tmp_path, monkeypatch):
    (tmp_path / "001_ok.sql").write_text(
        "create schema if not exists rehoboam;\ncreate table rehoboam.t_ok (x integer);\n"
    )
    (tmp_path / "002_bad.sql").write_text("this is not sql;\n")
    monkeypatch.setattr("rehoboam.store.migrate.MIGRATIONS", tmp_path)
    # The exception must propagate out of connect()'s own __exit__ (not be
    # swallowed while still inside it) — connect() commits on a clean exit,
    # which would mask the bug this test exists to catch. See it fail for
    # real by checking a brand-new connection afterward.
    with pytest.raises(psycopg.Error):
        with connect(blank_dsn) as conn:
            migrate(conn)
    with connect(blank_dsn) as conn:
        assert applied_versions(conn) == {1}
        exists = conn.execute(
            "select 1 from information_schema.tables "
            "where table_schema = 'rehoboam' and table_name = 't_ok'"
        ).fetchone()
    assert exists is not None


def test_migrate_refreshes_the_bot_role_grants_on_new_tables(store_dsn, tmp_path, monkeypatch):
    with connect(store_dsn) as conn:
        migrate(conn)
        bootstrap(conn, "pw")
        # A table created by the connecting superuser would pick up rehoboam_bot's
        # grant via bootstrap()'s "alter default privileges ... for role <superuser>",
        # masking whether refresh_grants() itself did anything — a second role that
        # owns the new table is the only way to prove the fix, not this artifact.
        exists = conn.execute("select 1 from pg_roles where rolname = 'other_admin'").fetchone()
        if not exists:
            conn.execute("create role other_admin")
        conn.execute("grant usage, create on schema rehoboam to other_admin")
        conn.commit()
        # store_dsn already has versions 1-13 applied from the real
        # migrations dir; use 014 so this simulated file is genuinely new.
        (tmp_path / "014_simulated.sql").write_text(
            "set role other_admin;\ncreate table rehoboam.t_new (x integer);\nreset role;\n"
        )
        monkeypatch.setattr("rehoboam.store.migrate.MIGRATIONS", tmp_path)
        migrate(conn)
        ok = conn.execute(
            "select has_table_privilege('rehoboam_bot', 'rehoboam.t_new', 'INSERT') as ok"
        ).fetchone()["ok"]
    assert ok


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


def test_an_up_to_date_database_needs_only_select_from_the_bot_role(store_dsn):
    with connect(store_dsn) as conn:
        migrate(conn)
        bootstrap(conn, "pw")
        conn.commit()
        conn.execute(f"set role {ROLE}")
        conn.commit()
        try:
            assert applied_versions(conn) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
            assert migrate(conn) == []
        finally:
            conn.execute("reset role")


def test_migrate_applies_002_and_creates_player_status_daily(blank_dsn):
    from rehoboam.store import SCHEMA, connect
    from rehoboam.store.migrate import migrate

    with connect(blank_dsn) as conn:
        applied = migrate(conn)
        exists = conn.execute(
            "select to_regclass(%s) as t", (f"{SCHEMA}.player_status_daily",)
        ).fetchone()["t"]
        col = conn.execute(
            "select 1 from information_schema.columns where table_schema = %s "
            "and table_name = 'sweep_progress' and column_name = 'status_fetched_at'",
            (SCHEMA,),
        ).fetchone()
    assert "002_player_status_daily.sql" in applied
    assert exists is not None and col is not None


def test_applying_a_new_file_under_the_bot_role_fails_clearly(store_dsn, tmp_path, monkeypatch):
    with connect(store_dsn) as conn:
        migrate(conn)
        bootstrap(conn, "pw")
        conn.commit()
        # store_dsn already has versions 1-13 applied from the real
        # migrations dir; use 014 so this simulated file is genuinely new.
        (tmp_path / "014_simulated.sql").write_text("create table rehoboam.t_new (x integer);\n")
        monkeypatch.setattr("rehoboam.store.migrate.MIGRATIONS", tmp_path)
        conn.execute(f"set role {ROLE}")
        conn.commit()
        try:
            with pytest.raises(PermissionError) as excinfo:
                migrate(conn)
            assert "postgres admin" in str(excinfo.value)
            assert isinstance(excinfo.value.__cause__, psycopg.errors.InsufficientPrivilege)
        finally:
            conn.execute("reset role")
        assert applied_versions(conn) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}


def test_migrate_applies_008_with_the_mv_change_column(blank_dsn):
    with connect(blank_dsn) as conn:
        applied = migrate(conn)
        col = conn.execute(
            "select data_type from information_schema.columns where table_schema = %s "
            "and table_name = 'player_status_daily' and column_name = 'mv_change'",
            (SCHEMA,),
        ).fetchone()
    assert "008_mv_forecast.sql" in applied
    assert col is not None and col["data_type"] == "bigint"
