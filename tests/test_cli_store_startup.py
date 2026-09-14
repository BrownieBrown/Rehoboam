"""auto and status refuse to start without a reachable, migrated store."""

from __future__ import annotations

import psycopg
from typer.testing import CliRunner

from rehoboam.cli import app

runner = CliRunner()


def test_status_without_database_url_fails_before_login(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output
    assert "Traceback" not in result.output


def test_auto_without_database_url_fails_before_login(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    result = runner.invoke(app, ["auto", "--dry-run"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output


def test_status_reports_wrong_role_without_traceback(monkeypatch):
    """A role with no USAGE on the ``rehoboam`` schema fails with
    ``psycopg.errors.InsufficientPrivilege`` -- a ``ProgrammingError``, not
    one of ``_ensure_store``'s named cases -- so it needs the catch-all
    ``except psycopg.Error`` clause to print one red line instead of a
    traceback.
    """
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")

    def _raise(dsn=None):
        raise psycopg.errors.InsufficientPrivilege("permission denied for schema rehoboam")

    monkeypatch.setattr("rehoboam.store.ensure_ready", _raise)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "store error" in result.output
    assert "Traceback" not in result.output
