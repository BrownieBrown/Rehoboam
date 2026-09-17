"""The ingestion's forecast step (spec 2026-09-17): score, then forecast.

Runs after the per-player loop, so today's readings are in the store. Scoring
comes first because the morning's readings are what score yesterday's
forecasts. The step never raises: a failure is logged and returned, and the
run's other work stands.

`player_status_daily.day` is keyed by the UTC date of the ingestion run
(`rehoboam/enrichment/ingest.py`), while this step works in Berlin dates. The
two scheduled runs (05:00/17:00 UTC) fall on the same calendar date in both
zones all year, so they are unaffected. A manual `rehoboam ingest` between
22:00 UTC (summer) or 23:00 UTC (winter) and midnight UTC writes under the
previous day's key and overwrites that day's pre-update reading, so avoid
running it in that window.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from rehoboam.services.mv_forecast import (
    METHOD,
    SCORED,
    UNSCORABLE,
    Score,
    berlin_today,
    forecast,
    score,
    usable_day,
)

logger = logging.getLogger(__name__)


@dataclass
class MvForecastOutcome:
    written: int = 0
    scored: int = 0
    unscorable: int = 0
    error: str | None = None


def _score_pending(store, today: date, now: float) -> tuple[int, int]:
    by_next_day: dict[date, list[dict]] = defaultdict(list)
    for row in store.pending(before=today):
        by_next_day[row["target_day"] + timedelta(days=1)].append(row)

    outcomes: list[tuple[str, date, Score]] = []
    for next_day, rows in sorted(by_next_day.items()):
        readings = store.status_readings(next_day, [r["player_id"] for r in rows])
        for row in rows:
            reading = readings.get(row["player_id"])
            usable = (
                reading is not None
                and reading["market_value"] is not None
                and reading["mv_change"] is not None
                and usable_day(reading["fetched_at"]) == next_day
            )
            if usable:
                result = score(row["base_mv"], reading["market_value"], reading["mv_change"])
            elif next_day < today:
                result = Score(UNSCORABLE, None, None)
            else:
                continue  # today's afternoon run may still read it
            outcomes.append((row["player_id"], row["target_day"], result))

    store.record_outcomes(outcomes, scored_at=now)
    scored = sum(1 for _, _, s in outcomes if s.outcome == SCORED)
    return scored, len(outcomes) - scored


def run_mv_forecast(store, *, now: float, momentum: float, cap: float) -> MvForecastOutcome:
    outcome = MvForecastOutcome()
    try:
        today = berlin_today(now)
        outcome.scored, outcome.unscorable = _score_pending(store, today, now)
        forecasts = []
        for row in store.status_rows(today):
            if usable_day(row["fetched_at"]) != today:
                continue
            f = forecast(
                row["player_id"],
                row["market_value"],
                row["mv_change"],
                today,
                momentum=momentum,
                cap=cap,
            )
            if f is not None:
                forecasts.append(f)
        outcome.written = store.upsert_forecasts(forecasts, made_at=now, method=METHOD)
    except Exception as e:  # noqa: BLE001 — the step reports, the run goes on
        logger.exception("mv forecast step failed")
        outcome.error = str(e)[:500] or type(e).__name__
    logger.info(
        "mv-forecast written=%d scored=%d unscorable=%d error=%s",
        outcome.written,
        outcome.scored,
        outcome.unscorable,
        outcome.error,
    )
    return outcome
