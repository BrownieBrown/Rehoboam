"""Azure Functions handler for func-rehoboam-external: ingestion and the weekly export.

Mirrors deploy/azure_function/function_app.py: heavy imports inside the
handlers, /tmp as the working directory, and `ensure_ready()` before any
work — a run against a half-migrated store must not start.
"""

import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import azure.functions as func

app = func.FunctionApp()
sys.path.insert(0, str(Path(__file__).parent))
TEMP_DIR = "/tmp"


def _prepare() -> None:
    os.chdir(TEMP_DIR)
    os.makedirs(f"{TEMP_DIR}/logs", exist_ok=True)


# 05:00 and 17:00 UTC — three hours before each trading session (spec §2).
@app.timer_trigger(schedule="0 0 5,17 * * *", arg_name="timer", run_on_startup=False)
def ingest(timer: func.TimerRequest):
    from rehoboam.api import KickbaseAPI
    from rehoboam.config import get_settings
    from rehoboam.enrichment.ingest import IngestBudget, facts_for_ingest, run_ingestion
    from rehoboam.services.session_facts import SessionFacts
    from rehoboam.store import ensure_ready
    from rehoboam.store.corpus_store import CorpusStore
    from rehoboam.store.session_store import SessionStore

    _prepare()
    logging.info("ingestion-start")
    session_id = uuid.uuid4().hex[:12]
    started_at = time.time()
    try:
        ensure_ready()
        settings = get_settings()
        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        api.login()
        leagues = api.get_leagues()
        if not leagues:
            logging.error("ingestion: no leagues found")
            return
        league = leagues[int(os.getenv("LEAGUE_INDEX", "0"))]
        budget = IngestBudget(
            deadline=time.time() + settings.ingest_deadline_seconds,
            max_requests=settings.ingest_max_requests,
        )
        stats = run_ingestion(
            api.client,
            CorpusStore(),
            league_id=league.id,
            budget=budget,
            stale_after_seconds=settings.ingest_stale_after_hours * 3600.0,
            mv_stale_after_seconds=settings.ingest_mv_stale_after_hours * 3600.0,
            status_stale_after_seconds=settings.ingest_status_stale_after_hours * 3600.0,
        )
        try:
            SessionStore().record(facts_for_ingest(stats, app="external", session_id=session_id))
        except Exception:
            logging.error("session_facts write failed", exc_info=True)
    except Exception as e:
        logging.error(f"Ingestion failed: {e}", exc_info=True)
        try:
            SessionStore().record(
                SessionFacts(
                    session_id=session_id,
                    app="external",
                    mode="ingest",
                    started_at=started_at,
                    duration_s=time.time() - started_at,
                    errors=1,
                    error_text=str(e)[:2000],
                )
            )
        except Exception:
            logging.error("session_facts write failed", exc_info=True)


# Sunday 03:00 UTC, when nothing else runs.
@app.timer_trigger(schedule="0 0 3 * * 0", arg_name="timer", run_on_startup=False)
def weekly_export(timer: func.TimerRequest):
    from rehoboam.services.session_facts import SessionFacts
    from rehoboam.store import connect, ensure_ready
    from rehoboam.store.export import blob_uploader, export_tables, facts_for_export
    from rehoboam.store.session_store import SessionStore

    _prepare()
    logging.info("export-start")
    session_id = uuid.uuid4().hex[:12]
    started_at = time.time()
    try:
        ensure_ready()
        upload = blob_uploader(
            os.environ["AZURE_STORAGE_CONNECTION_STRING"],
            os.getenv("BLOB_CONTAINER", "rehoboam-data"),
        )
        with connect() as conn:
            sizes = export_tables(conn, upload, day=datetime.now(tz=timezone.utc).date())
        logging.info("export-end tables=%d bytes=%d", len(sizes), sum(sizes.values()))
        try:
            SessionStore().record(
                facts_for_export(
                    sizes,
                    app="external",
                    session_id=session_id,
                    started_at=started_at,
                    duration_s=time.time() - started_at,
                )
            )
        except Exception:
            logging.error("session_facts write failed", exc_info=True)
    except Exception as e:
        logging.error(f"Export failed: {e}", exc_info=True)
        try:
            SessionStore().record(
                SessionFacts(
                    session_id=session_id,
                    app="external",
                    mode="export",
                    started_at=started_at,
                    duration_s=time.time() - started_at,
                    errors=1,
                    error_text=str(e)[:2000],
                )
            )
        except Exception:
            logging.error("session_facts write failed", exc_info=True)
