"""The ingest and export commands refuse to start without what they need, up front.

Also: a completed run of either command leaves a `session_facts` row behind
(`app="cli"`) -- rule I7 reads these rows across both apps to know whether
ingestion is keeping up.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from typer.testing import CliRunner

from rehoboam.cli import app
from rehoboam.enrichment.ingest import IngestStats
from rehoboam.store import connect
from rehoboam.store.session_store import SessionStore

runner = CliRunner()


def _latest_facts(dsn: str, *, app_name: str, mode: str) -> dict | None:
    """Look up the row by (app, mode) instead of session_id: the CLI mints its
    own uuid internally, so a test asserting on the row can't predict it."""
    with connect(dsn) as conn:
        row = conn.execute(
            "SELECT * FROM rehoboam.session_facts WHERE app = %s AND mode = %s "
            "ORDER BY started_at DESC LIMIT 1",
            (app_name, mode),
        ).fetchone()
    return dict(row) if row else None


def test_ingest_without_database_url_fails_before_login(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output and "Traceback" not in result.output


def test_ingest_records_a_session_facts_row(monkeypatch, store_dsn):
    fake_league = SimpleNamespace(id="L1")
    fake_settings = SimpleNamespace(
        ingest_deadline_seconds=60.0,
        ingest_max_requests=100,
        ingest_stale_after_hours=24.0,
        ingest_mv_stale_after_hours=168.0,
        ingest_status_stale_after_hours=3.0,
        ingest_transfers_stale_after_hours=168.0,
        mv_forecast_momentum=0.9,
        mv_forecast_cap=0.2,
    )
    fake_api = SimpleNamespace(client=object(), user=SimpleNamespace(id="u1"))
    monkeypatch.setattr(
        "rehoboam.cli._login_and_get_league",
        lambda league_index: (fake_api, fake_settings, fake_league),
    )
    canned_stats = IngestStats(
        universe_size=500,
        status_written=480,
        performance_fetched=120,
        mv_fetched=60,
        failed=3,
        requests=663,
        stopped_by=None,
        started_at=time.time(),
        duration_s=42.0,
    )
    monkeypatch.setattr("rehoboam.enrichment.ingest.run_ingestion", lambda *a, **kw: canned_stats)
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0, result.output
    row = _latest_facts(store_dsn, app_name="cli", mode="ingest")
    assert row is not None
    assert row["errors"] == 0
    assert row["extra"]["status_written"] == 480
    assert row["extra"]["failed"] == 3
    assert row["extra"]["mv_forecast"] == {
        "written": 0,
        "scored": 0,
        "unscorable": 0,
        "error": None,
    }
    assert "mv_forecast" in result.output


def test_mv_nightly_records_a_session_facts_row_with_that_mode(monkeypatch, store_dsn):
    fake_league = SimpleNamespace(id="L1")
    fake_settings = SimpleNamespace(
        ingest_deadline_seconds=60.0,
        ingest_max_requests=100,
        mv_forecast_momentum=0.9,
        mv_forecast_cap=0.2,
    )
    fake_api = SimpleNamespace(client=object(), user=SimpleNamespace(id="u1"))
    monkeypatch.setattr(
        "rehoboam.cli._login_and_get_league",
        lambda league_index: (fake_api, fake_settings, fake_league),
    )
    canned_stats = IngestStats(
        universe_size=500,
        status_written=500,
        requests=500,
        stopped_by=None,
        started_at=time.time(),
        duration_s=12.0,
    )
    captured: dict = {}

    def _fake_run_ingestion(*a, **kw):
        captured.update(kw)
        return canned_stats

    monkeypatch.setattr("rehoboam.enrichment.ingest.run_ingestion", _fake_run_ingestion)
    result = runner.invoke(app, ["mv-nightly"])
    assert result.exit_code == 0, result.output
    # Pin the arguments that keep this pass to status for every player and
    # nothing else, and skip the league refresh -- a dropped `league_store=None`
    # or `status_stale_after_seconds` would leave this suite green while the
    # nightly did a full pass.
    assert captured["status_stale_after_seconds"] == 0.0
    assert captured["stale_after_seconds"] == 10 * 86400
    assert captured["mv_stale_after_seconds"] == 10 * 86400
    assert captured["transfers_stale_after_seconds"] == 10 * 86400
    assert captured["league_store"] is None
    assert captured["learner"] is None
    row = _latest_facts(store_dsn, app_name="cli", mode="mv_nightly")
    assert row is not None
    assert row["errors"] == 0
    assert row["extra"]["status_written"] == 500
    assert row["extra"]["mv_forecast"] == {
        "written": 0,
        "scored": 0,
        "unscorable": 0,
        "error": None,
    }
    assert "mv_forecast" in result.output


def test_mv_nightly_clamps_the_deadline_to_540s_by_default(monkeypatch, store_dsn):
    """21:45 UTC + up to 9 min must not cross Berlin midnight (22:00 UTC), or
    a slow run's readings key to the wrong day. A settings default far above
    540s must still be clamped when --deadline-seconds is not given."""
    fake_league = SimpleNamespace(id="L1")
    fake_settings = SimpleNamespace(
        ingest_deadline_seconds=6_000.0,
        ingest_max_requests=100,
        mv_forecast_momentum=0.9,
        mv_forecast_cap=0.2,
    )
    fake_api = SimpleNamespace(client=object(), user=SimpleNamespace(id="u1"))
    monkeypatch.setattr(
        "rehoboam.cli._login_and_get_league",
        lambda league_index: (fake_api, fake_settings, fake_league),
    )
    canned_stats = IngestStats(started_at=time.time(), duration_s=1.0)
    captured: dict = {}

    def _fake_run_ingestion(*a, **kw):
        captured.update(kw)
        return canned_stats

    monkeypatch.setattr("rehoboam.enrichment.ingest.run_ingestion", _fake_run_ingestion)
    before = time.time()
    result = runner.invoke(app, ["mv-nightly"])
    assert result.exit_code == 0, result.output
    deadline = captured["budget"].deadline
    assert deadline <= before + 540.0 + 5.0  # clamped, not the 6,000s default
    assert deadline >= before + 540.0 - 5.0


def test_mv_nightly_honours_an_explicit_deadline_seconds_unclamped(monkeypatch, store_dsn):
    """An operator who explicitly asks for a longer run (e.g. a catch-up)
    still gets it -- only the *default* is clamped."""
    fake_league = SimpleNamespace(id="L1")
    fake_settings = SimpleNamespace(
        ingest_deadline_seconds=60.0,
        ingest_max_requests=100,
        mv_forecast_momentum=0.9,
        mv_forecast_cap=0.2,
    )
    fake_api = SimpleNamespace(client=object(), user=SimpleNamespace(id="u1"))
    monkeypatch.setattr(
        "rehoboam.cli._login_and_get_league",
        lambda league_index: (fake_api, fake_settings, fake_league),
    )
    canned_stats = IngestStats(started_at=time.time(), duration_s=1.0)
    captured: dict = {}

    def _fake_run_ingestion(*a, **kw):
        captured.update(kw)
        return canned_stats

    monkeypatch.setattr("rehoboam.enrichment.ingest.run_ingestion", _fake_run_ingestion)
    before = time.time()
    result = runner.invoke(app, ["mv-nightly", "--deadline-seconds", "3600"])
    assert result.exit_code == 0, result.output
    deadline = captured["budget"].deadline
    assert deadline >= before + 3600.0 - 5.0


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
    row = _latest_facts(store_dsn, app_name="cli", mode="export")
    assert row is not None
    assert row["errors"] == 0
    assert row["extra"]["tables"] > 0
    assert row["extra"]["bytes"] >= 0


def test_a_session_facts_row_exists_via_sessionstore_facts(monkeypatch, store_dsn):
    """The same row is reachable through SessionStore's own API, not just raw SQL --
    a sanity check that the CLI writes through the shared store class."""
    fake_league = SimpleNamespace(id="L1")
    fake_settings = SimpleNamespace(
        ingest_deadline_seconds=60.0,
        ingest_max_requests=100,
        ingest_stale_after_hours=24.0,
        ingest_mv_stale_after_hours=168.0,
        ingest_status_stale_after_hours=3.0,
        ingest_transfers_stale_after_hours=168.0,
        mv_forecast_momentum=0.9,
        mv_forecast_cap=0.2,
    )
    fake_api = SimpleNamespace(client=object(), user=SimpleNamespace(id="u1"))
    monkeypatch.setattr(
        "rehoboam.cli._login_and_get_league",
        lambda league_index: (fake_api, fake_settings, fake_league),
    )
    canned_stats = IngestStats(
        universe_size=1,
        status_written=1,
        performance_fetched=1,
        mv_fetched=1,
        failed=0,
        requests=3,
        stopped_by=None,
        started_at=time.time(),
        duration_s=1.0,
    )
    monkeypatch.setattr("rehoboam.enrichment.ingest.run_ingestion", lambda *a, **kw: canned_stats)
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0, result.output
    session_id = _latest_facts(store_dsn, app_name="cli", mode="ingest")["session_id"]
    row = SessionStore(dsn=store_dsn).facts(session_id)
    assert row["app"] == "cli" and row["mode"] == "ingest" and row["errors"] == 0
