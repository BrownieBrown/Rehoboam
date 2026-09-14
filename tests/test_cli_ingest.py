"""The ingest and export commands refuse to start without what they need, up front."""

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


def test_export_without_connection_string_fails_before_uploading(monkeypatch, store_dsn):
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    result = runner.invoke(app, ["export"])
    assert result.exit_code == 1
    assert "AZURE_STORAGE_CONNECTION_STRING is not set" in result.output


def test_export_uploads_every_table_and_prints_their_names(monkeypatch, store_dsn):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "unused-in-this-test")
    uploaded: dict[str, bytes] = {}
    monkeypatch.setattr(
        "rehoboam.store.export.blob_uploader",
        lambda connection_string, container: uploaded.__setitem__,
    )
    result = runner.invoke(app, ["export"])
    assert result.exit_code == 0, result.output
    assert "schema_migrations" in result.output
    assert "exports/" in next(iter(uploaded))
