"""One row per run, failures per session, and the last completed ingest for I7."""

from __future__ import annotations

from rehoboam.services.session_facts import IntegrityFailure, SessionFacts
from rehoboam.store.session_store import SessionStore


def _facts(**over) -> SessionFacts:
    base = {
        "session_id": "s1",
        "app": "function",
        "mode": "lineup_only",
        "dry_run": False,
        "started_at": 1_000.0,
        "duration_s": 12.5,
        "phase": "moderate",
        "next_kickoff": 90_000.0,
        "next_kickoff_source": "schedule",
        "squad_gk": 1,
        "squad_def": 4,
        "squad_mid": 4,
        "squad_fw": 2,
        "fieldable_count": 11,
        "legal_formation": "4-4-2",
        "budget": 5_000_000,
        "sellable_value": 150_000_000,
        "open_offers_total": 0,
        "open_offers_manual": 0,
        "cost_basis_missing": 0,
        "predictions_written": 15,
        "lineup_result": "set",
        "errors": 0,
        "error_text": "",
    }
    base.update(over)
    return SessionFacts(**base)


def test_record_is_an_upsert_on_session_id(store_dsn):
    store = SessionStore(dsn=store_dsn)
    store.record(_facts())
    store.record(_facts(duration_s=20.0, errors=1, error_text="lineup: boom"))
    row = store.facts("s1")
    assert (row["duration_s"], row["errors"], row["error_text"]) == (
        20.0,
        1,
        "lineup: boom",
    )
    assert row["legal_formation"] == "4-4-2" and row["next_kickoff_source"] == "schedule"


def test_failures_are_recorded_per_session(store_dsn):
    store = SessionStore(dsn=store_dsn)
    store.record(_facts())
    n = store.record_failures(
        "s1",
        [
            IntegrityFailure("I1", "next kickoff unknown"),
            IntegrityFailure("I5", "0 rows"),
        ],
        at=2_000.0,
    )
    assert n == 2
    assert [f["rule"] for f in store.failures("s1")] == ["I1", "I5"]
    assert store.failures("nope") == []


def test_last_ingest_completed_at_ignores_failed_runs_and_other_modes(store_dsn):
    store = SessionStore(dsn=store_dsn)
    # Not filtered by `app` -- a manual `rehoboam ingest` catch-up (app
    # "cli") counts the same as the scheduled external one, so the row
    # excluded here is excluded by `mode`, not by `app`.
    store.record(
        _facts(session_id="t1", app="external", mode="export", started_at=5_000.0, duration_s=10.0)
    )
    store.record(
        _facts(
            session_id="i1",
            app="external",
            mode="ingest",
            started_at=3_000.0,
            duration_s=400.0,
        )
    )
    store.record(
        _facts(
            session_id="i2",
            app="external",
            mode="ingest",
            started_at=4_000.0,
            duration_s=100.0,
            errors=1,
        )
    )
    assert store.last_ingest_completed_at() == 3_400.0


def test_last_ingest_completed_at_is_none_when_nothing_ran(store_dsn):
    assert SessionStore(dsn=store_dsn).last_ingest_completed_at() is None


def test_extra_is_stored_as_json(store_dsn):
    store = SessionStore(dsn=store_dsn)
    store.record(
        _facts(
            session_id="i1",
            app="external",
            mode="ingest",
            extra={"status_written": 462},
        )
    )
    assert store.facts("i1")["extra"] == {"status_written": 462}


def test_extra_none_round_trips_as_sql_null(store_dsn):
    """`extra=None` must go over the wire as SQL NULL, not the JSON value
    `null` -- Jsonb(None) would serialize to the latter, and `facts()`
    would then read back a JSON null instead of Python `None`."""
    store = SessionStore(dsn=store_dsn)
    store.record(_facts(session_id="i1", extra=None))
    row = store.facts("i1")
    assert row["extra"] is None
