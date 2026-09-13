"""auto and status refuse to start without a reachable, migrated store."""

from __future__ import annotations

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
