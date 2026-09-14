"""The ingest command refuses to start without the store, before any login."""

from __future__ import annotations

from typer.testing import CliRunner

from rehoboam.cli import app

runner = CliRunner()


def test_ingest_without_database_url_fails_before_login(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output and "Traceback" not in result.output
