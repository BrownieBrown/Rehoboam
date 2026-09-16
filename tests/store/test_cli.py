"""The four store commands do what their names say, against a real database."""

from __future__ import annotations

import sqlite3

from typer.testing import CliRunner

from rehoboam.cli import app
from rehoboam.config import Settings
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.store import SCHEMA, connect

runner = CliRunner()


def test_migrate_command_applies_then_reports_nothing_to_do(blank_dsn):
    first = runner.invoke(app, ["migrate", "--dsn", blank_dsn])
    assert first.exit_code == 0, first.output
    assert "001_schema.sql" in first.output
    second = runner.invoke(app, ["migrate", "--dsn", blank_dsn])
    assert second.exit_code == 0
    assert "none" in second.output


def test_db_bootstrap_creates_the_role_with_rights_on_the_schema(store_dsn):
    runner.invoke(app, ["migrate", "--dsn", store_dsn])
    result = runner.invoke(
        app, ["db-bootstrap", "--admin-dsn", store_dsn, "--role-password", "s3cret"]
    )
    assert result.exit_code == 0, result.output
    assert "s3cret" not in result.output
    with connect(store_dsn) as conn:
        role = conn.execute(
            "select rolcanlogin from pg_roles where rolname = 'rehoboam_bot'"
        ).fetchone()
        assert role and role["rolcanlogin"]
        ok = conn.execute(
            "select has_table_privilege('rehoboam_bot', %s, 'INSERT') as ok",
            (f"{SCHEMA}.pending_bids",),
        ).fetchone()["ok"]
        assert ok
    again = runner.invoke(
        app, ["db-bootstrap", "--admin-dsn", store_dsn, "--role-password", "s3cret"]
    )
    assert again.exit_code == 0
    # The second run did not apply --role-password; say so, or an operator
    # believes a rotation happened and the store's real password drifts.
    assert "password unchanged" in again.output


def test_import_and_pull_commands_round_trip(store_dsn, tmp_path):
    corpus = tmp_path / "training_corpus.db"
    TrainingCorpus(corpus)
    with sqlite3.connect(corpus) as db:
        db.execute(
            "insert into mv_series (player_id, snapshot_at, market_value) values ('p', 1.0, 9)"
        )
        db.execute("alter table mv_series add column legacy_col text")
        db.commit()
    runner.invoke(app, ["migrate", "--dsn", store_dsn])
    imported = runner.invoke(
        app,
        [
            "import-sqlite",
            "--dsn",
            store_dsn,
            "--learning",
            str(tmp_path / "absent.db"),
            "--corpus",
            str(corpus),
            "--cache",
            str(tmp_path / "absent2.db"),
        ],
    )
    assert imported.exit_code == 0, imported.output
    assert "mv_series" in imported.output
    assert "legacy_col" in imported.output
    assert (
        "Some source columns have no home in the store — see the skipped columns above."
        in imported.output
    )
    out = tmp_path / "pulled.db"
    learning_out = tmp_path / "pulled_learning.db"
    pulled = runner.invoke(
        app,
        [
            "corpus-pull",
            "--dsn",
            store_dsn,
            "--out",
            str(out),
            "--learning-out",
            str(learning_out),
        ],
    )
    assert pulled.exit_code == 0, pulled.output
    with sqlite3.connect(out) as db:
        assert db.execute("select market_value from mv_series").fetchone()[0] == 9
    assert learning_out.exists()


def test_store_commands_fail_cleanly_when_database_url_is_unset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", tmp_path / ".env")
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output
    assert "Traceback" not in result.output


def test_calibrate_dry_run_reports_nothing_written(store_dsn, monkeypatch):
    from unittest.mock import patch

    schedule = {"it": [{"day": 1, "it": [{"dt": "2026-08-22T18:30:00Z", "st": 2}]}]}
    api = type(
        "Api",
        (),
        {"get_competition_matchdays": lambda self, competition_id="1": schedule},
    )()
    with patch("rehoboam.cli._login_and_get_league", return_value=(api, None, None)):
        result = runner.invoke(app, ["calibrate", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "no season" in result.output or "dry run" in result.output


def test_players_prints_the_view(store_dsn):
    from tests.store.test_player_table import _seed

    _seed(store_dsn)
    result = runner.invoke(app, ["players", "--position", "Midfielder"])
    assert result.exit_code == 0, result.output
    assert "Alpha" in result.output and "Rival" in result.output and "Club Seven" in result.output


def test_players_bad_sort_prints_a_message_instead_of_a_traceback(store_dsn):
    result = runner.invoke(app, ["players", "--sort", "nope"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "nope" in result.output or "order_by" in result.output


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
        n = conn.execute("SELECT count(*) AS n FROM rehoboam.manager_transfers").fetchone()["n"]
    assert n == 52  # 26 per manager × 2 managers
