"""The two outside sources, run after the ingest loop (2026-10-08).

Understat (xG table, weekly) and ligainsider (predicted elevens, within
`PREDICTED_XI_DAYS_BEFORE` of kickoff). Each step logs and skips on failure;
the outcomes ride into `session_facts.extra["outside_sources"]`.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_outside_sources(settings, *, season: str | None, now: float) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    if season and getattr(settings, "understat_enabled", True):
        try:
            from rehoboam.enrichment.understat import run_understat_refresh
            from rehoboam.store.understat_store import UnderstatStore

            outcome["understat"] = run_understat_refresh(
                UnderstatStore(),
                season=season,
                now=now,
                stale_after_s=float(settings.understat_stale_after_hours) * 3600.0,
            )
        except Exception as e:  # noqa: BLE001 -- never fails the run
            logger.exception("understat step failed")
            outcome["understat"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    if float(getattr(settings, "predicted_xi_days_before", 0.0)) > 0:
        try:
            from rehoboam.enrichment.ligainsider import run_predicted_xi_refresh
            from rehoboam.store.lineup_store import PredictedLineupStore

            outcome["predicted_xi"] = run_predicted_xi_refresh(
                PredictedLineupStore(),
                now=now,
                days_before=float(settings.predicted_xi_days_before),
                throttle_seconds=float(settings.outside_source_throttle_seconds),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("predicted xi step failed")
            outcome["predicted_xi"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    logger.info("outside-sources-end %s", outcome)
    return outcome
