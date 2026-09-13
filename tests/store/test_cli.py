"""The four store commands do what their names say, against a real database."""

from __future__ import annotations

import sqlite3

from typer.testing import CliRunner

from rehoboam.cli import app
from rehoboam.config import Settings
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.store import SCHEMA, connect

runner = CliRunner()


def test_migrate_command_applies_then_reports_nothing_to_do(store_dsn):
    first = runner.invoke(app, ["migrate", "--dsn", store_dsn])
    assert first.exit_code == 0, first.output
    assert "001_schema.sql" in first.output
    second = runner.invoke(app, ["migrate", "--dsn", store_dsn])
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
    pulled = runner.invoke(app, ["corpus-pull", "--dsn", store_dsn, "--out", str(out)])
    assert pulled.exit_code == 0, pulled.output
    with sqlite3.connect(out) as db:
        assert db.execute("select market_value from mv_series").fetchone()[0] == 9


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
