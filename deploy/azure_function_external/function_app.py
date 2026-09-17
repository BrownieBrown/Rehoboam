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
from dataclasses import asdict
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
    from rehoboam.bid_learner import BidLearner
    from rehoboam.config import get_settings
    from rehoboam.enrichment.ingest import IngestBudget, facts_for_ingest, run_ingestion
    from rehoboam.services.session_facts import SessionFacts
    from rehoboam.store import ensure_ready
    from rehoboam.store.corpus_store import CorpusStore
    from rehoboam.store.league_store import LeagueStore
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

        from rehoboam.enrichment.calibrate import prepare_refresh, run_calibration
        from rehoboam.store.calibration_store import CalibrationStore

        corpus = CorpusStore()
        calibration_store = CalibrationStore()
        schedule = None
        season = None
        try:
            schedule = api.get_competition_matchdays()
            season = calibration_store.current_season()
            if season:
                cleared = prepare_refresh(
                    calibration_store, corpus, schedule, season=season, now=time.time()
                )
                if cleared:
                    logging.info("calibration: cleared fetch stamps %s", cleared)
        except Exception:
            logging.warning("calibration: pre-ingest step failed", exc_info=True)

        budget = IngestBudget(
            deadline=started_at + settings.ingest_deadline_seconds,
            max_requests=settings.ingest_max_requests,
        )
        stats = run_ingestion(
            api.client,
            corpus,
            league_id=league.id,
            budget=budget,
            stale_after_seconds=settings.ingest_stale_after_hours * 3600.0,
            mv_stale_after_seconds=settings.ingest_mv_stale_after_hours * 3600.0,
            status_stale_after_seconds=settings.ingest_status_stale_after_hours * 3600.0,
            transfers_stale_after_seconds=settings.ingest_transfers_stale_after_hours * 3600.0,
            league_store=LeagueStore(),
            learner=BidLearner(),
            our_user_id=str(api.user.id),
            season=season,
        )

        calibration = None
        if schedule is not None and season:
            telegram = (
                (settings.telegram_bot_token, settings.telegram_chat_id)
                if settings.telegram_bot_token and settings.telegram_chat_id
                else None
            )
            outcome = run_calibration(
                calibration_store, schedule, season=season, now=time.time(), telegram=telegram
            )
            calibration = asdict(outcome)
            logging.info("calibration-end %s", calibration)

        mv_forecast = None
        try:
            from rehoboam.enrichment.mv_forecast import run_mv_forecast
            from rehoboam.store.mv_forecast_store import MvForecastStore

            mv_forecast = asdict(
                run_mv_forecast(
                    MvForecastStore(),
                    now=time.time(),
                    momentum=settings.mv_forecast_momentum,
                    cap=settings.mv_forecast_cap,
                )
            )
            logging.info("mv-forecast-end %s", mv_forecast)
        except Exception:
            logging.warning("mv forecast: step failed", exc_info=True)

        try:
            SessionStore().record(
                facts_for_ingest(
                    stats,
                    app="external",
                    session_id=session_id,
                    calibration=calibration,
                    mv_forecast=mv_forecast,
                )
            )
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


# 21:45 UTC — 23:45 Berlin in summer, 22:45 in winter: after Kickbase's ~22:00
# market-value move, so these readings say what it did. Status only: the full
# passes at 05:00/17:00 keep performance, MV series and transfers fresh.
@app.timer_trigger(schedule="0 45 21 * * *", arg_name="timer", run_on_startup=False)
def mv_nightly(timer: func.TimerRequest):
    from rehoboam.api import KickbaseAPI
    from rehoboam.config import get_settings
    from rehoboam.enrichment.ingest import IngestBudget, facts_for_ingest, run_ingestion
    from rehoboam.services.session_facts import SessionFacts
    from rehoboam.store import ensure_ready
    from rehoboam.store.corpus_store import CorpusStore
    from rehoboam.store.session_store import SessionStore

    _prepare()
    logging.info("mv-nightly-start")
    session_id = uuid.uuid4().hex[:12]
    started_at = time.time()
    try:
        ensure_ready()
        settings = get_settings()
        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        api.login()
        leagues = api.get_leagues()
        if not leagues:
            logging.error("mv-nightly: no leagues found")
            return
        league = leagues[int(os.getenv("LEAGUE_INDEX", "0"))]

        budget = IngestBudget(
            deadline=started_at + settings.ingest_deadline_seconds,
            max_requests=settings.ingest_max_requests,
        )
        stats = run_ingestion(
            api.client,
            CorpusStore(),
            league_id=league.id,
            budget=budget,
            stale_after_seconds=10 * 86400,
            mv_stale_after_seconds=10 * 86400,
            status_stale_after_seconds=0.0,
            transfers_stale_after_seconds=10 * 86400,
            league_store=None,
            learner=None,
        )

        mv_forecast = None
        try:
            from rehoboam.enrichment.mv_forecast import run_mv_forecast
            from rehoboam.store.mv_forecast_store import MvForecastStore

            mv_forecast = asdict(
                run_mv_forecast(
                    MvForecastStore(),
                    now=time.time(),
                    momentum=settings.mv_forecast_momentum,
                    cap=settings.mv_forecast_cap,
                )
            )
            logging.info("mv-forecast-end %s", mv_forecast)
        except Exception:
            logging.warning("mv forecast: step failed", exc_info=True)

        try:
            SessionStore().record(
                facts_for_ingest(
                    stats,
                    app="external",
                    session_id=session_id,
                    mv_forecast=mv_forecast,
                    mode="mv_nightly",
                )
            )
        except Exception:
            logging.error("session_facts write failed", exc_info=True)
    except Exception as e:
        logging.error(f"mv-nightly failed: {e}", exc_info=True)
        try:
            SessionStore().record(
                SessionFacts(
                    session_id=session_id,
                    app="external",
                    mode="mv_nightly",
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
