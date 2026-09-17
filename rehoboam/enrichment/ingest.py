"""The twice-daily league-wide pass (spec §2), within a budget.

Three reads per player — league player details (status + lineup
probability), competition performance (per-match history) and the
market-value series. Players are visited stalest player first, every stale
kind for that player, then the next player: a per-kind pass let status
starve performance and MV when the budget ran out mid-universe, since every
run always started the list over at the same kind. MV refreshes on its own,
wider window — it changes slowly and costs one request per player. The
budget is a wall-clock deadline and a request cap; a run that hits either
stops between requests, records why, and the next run starts with exactly
the players and kinds it did not reach, because progress is marked only
after a successful write.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from rehoboam.enrichment.budget import BudgetExhausted
from rehoboam.enrichment.league_refresh import run_league_refresh
from rehoboam.enrichment.sweep import fetch_universe
from rehoboam.services.session_facts import SessionFacts
from rehoboam.store.corpus_store import CorpusStore

logger = logging.getLogger(__name__)


@dataclass
class IngestBudget:
    deadline: float
    max_requests: int
    now: Callable[[], float] = time.time
    requests: int = 0

    def exhausted(self) -> str | None:
        if self.now() >= self.deadline:
            return "deadline"
        if self.requests >= self.max_requests:
            return "cap"
        return None

    def spend(self) -> None:
        """Account for one request about to be made; raise if the budget is gone."""
        reason = self.exhausted()
        if reason:
            raise BudgetExhausted(reason)
        self.requests += 1


@dataclass
class IngestStats:
    universe_size: int = 0
    status_written: int = 0
    performance_fetched: int = 0
    mv_fetched: int = 0
    transfers_fetched: int = 0
    failed: int = 0
    requests: int = 0
    stopped_by: str | None = None
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0
    league: dict | None = None


def facts_for_ingest(
    stats: IngestStats,
    *,
    app: str,
    session_id: str,
    calibration: dict | None = None,
    mv_forecast: dict | None = None,
    mode: str = "ingest",
) -> SessionFacts:
    """The row a completed ingest run leaves for rule I7.

    A per-player failure (`stats.failed`) is not a run failure on its own --
    the run itself reached its end, so `errors` stays 0 and the failure
    count rides along in `extra` instead, where it's visible without gating
    I7 on it. The exception is a run that failed on every player and wrote
    nothing at all: with none of the write counters above zero, `extra`
    would be the only evidence anything went wrong, and I7 would read this
    row as a completed ingest with nothing to show for it.

    `calibration`, when given, is the report step's `CalibrationOutcome` --
    it never affects `errors`, since `run_calibration` catches its own
    exceptions and reports them inside the outcome instead.

    `mv_forecast`, when given, is the forecast step's `MvForecastOutcome` as a
    dict -- like calibration, it never affects `errors`.

    `mode` defaults to `"ingest"`, what the twice-daily pass writes. The
    nightly pass (status for every player; nothing else refreshes, right
    after Kickbase's market-value update) reuses this helper but passes
    `mode="mv_nightly"`: rule I7 (`rehoboam/store/session_store.py`) counts
    only `mode = 'ingest'` rows, so the nightly pass must not claim to be
    one.
    """
    wrote_nothing = stats.failed and not (
        stats.status_written
        or stats.performance_fetched
        or stats.mv_fetched
        or stats.transfers_fetched
    )
    errors = 1 if wrote_nothing else 0
    error_text = f"{stats.failed} player(s) failed, nothing written" if wrote_nothing else ""
    extra = asdict(stats)
    if calibration is not None:
        extra["calibration"] = calibration
    if mv_forecast is not None:
        extra["mv_forecast"] = mv_forecast
    return SessionFacts(
        session_id=session_id,
        app=app,
        mode=mode,
        started_at=stats.started_at,
        duration_s=stats.duration_s,
        errors=errors,
        error_text=error_text,
        extra=extra,
    )


def _counting_client(client, budget: IngestBudget):
    """Wrap the client so every call spends one unit of budget first."""

    class _Counting:
        def __getattr__(self, name):
            attr = getattr(client, name)
            if not callable(attr):
                return attr

            def call(*a, **kw):
                budget.spend()
                return attr(*a, **kw)

            return call

    return _Counting()


def run_ingestion(
    client,
    store: CorpusStore,
    *,
    league_id: str,
    budget: IngestBudget,
    stale_after_seconds: float,
    mv_stale_after_seconds: float,
    status_stale_after_seconds: float | None = None,
    transfers_stale_after_seconds: float | None = None,
    throttle_seconds: float = 0.25,
    timeframe_days: int = 365,
    today: date | None = None,
    league_store=None,
    learner=None,
    our_user_id: str | None = None,
    season: str | None = None,
) -> IngestStats:
    stats = IngestStats(started_at=budget.now())
    day = today or datetime.now(tz=timezone.utc).date()
    api = _counting_client(client, budget)
    try:
        # One pooler connection (TLS + SCRAM) for the whole run instead of one
        # per store call — that handshake, not the Kickbase endpoints, was
        # where the per-request time went.
        with store.session():
            rows = fetch_universe(api, league_id, throttle_seconds=throttle_seconds)
            stats.universe_size = len(rows)
            store.upsert_players(rows)

            if league_store is not None and season:
                try:
                    league = run_league_refresh(
                        api,
                        league_store,
                        learner,
                        league_id=league_id,
                        our_user_id=our_user_id or "",
                        season=season,
                        now=budget.now(),
                    )
                    stats.league = asdict(league)
                except BudgetExhausted:
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.exception("league refresh failed (non-fatal)")
                    stats.league = {"error": str(e)}

            team_by_id = {r["player_id"]: r.get("team_id") for r in rows}

            now = budget.now()
            # Status is one cheap request per player and the reading a trading
            # session needs fresh, so it gets its own, shorter window: a run
            # three hours before a session re-reads it even when performance
            # (daily) and MV (weekly) are still fresh.
            status_window = (
                status_stale_after_seconds
                if status_stale_after_seconds is not None
                else stale_after_seconds
            )
            windows = {
                "status": now - status_window,
                "performance": now - stale_after_seconds,
                "mv": now - mv_stale_after_seconds,
            }
            fetchers = {
                "status": (
                    lambda pid: api.get_player_details(league_id=league_id, player_id=pid),
                    lambda pid, d: store.record_status_daily(pid, day, d, budget.now()),
                    "status_written",
                ),
                "performance": (
                    lambda pid: api.get_competition_player_performance(player_id=pid),
                    lambda pid, p: store.record_match_history(pid, team_by_id.get(pid), p),
                    "performance_fetched",
                ),
                "mv": (
                    lambda pid: api.get_player_market_value_history_v2(
                        player_id=pid, timeframe=timeframe_days
                    ),
                    lambda pid, h: store.record_mv_series(pid, h),
                    "mv_fetched",
                ),
            }
            if transfers_stale_after_seconds is not None:
                windows["transfers"] = now - transfers_stale_after_seconds
                fetchers["transfers"] = (
                    lambda pid: api.get_player_transfer_history(league_id=league_id, player_id=pid),
                    lambda pid, h: store.record_player_transfers(pid, h),
                    "transfers_fetched",
                )
            # Progress lives on `stats` directly, not a return value: a
            # BudgetExhausted unwind must not lose what this player already
            # wrote, and the tests require the counts to survive a stop.
            for pid, stale_kinds in store.players_needing_any_refresh(
                windows, player_ids=list(team_by_id)
            ):
                for kind in stale_kinds:
                    fetch, write, attr = fetchers[kind]
                    try:
                        payload = fetch(pid)
                        write(pid, payload)
                        store.mark_fetched(pid, at=budget.now(), **{kind: True})
                    except BudgetExhausted:
                        raise
                    except Exception as e:
                        stats.failed += 1
                        logger.warning("ingest %s failed for %s: %s", kind, pid, e)
                    else:
                        setattr(stats, attr, getattr(stats, attr) + 1)
                    if throttle_seconds:
                        time.sleep(throttle_seconds)
    except BudgetExhausted as stop:
        stats.stopped_by = stop.reason
    stats.requests = budget.requests
    stats.duration_s = budget.now() - stats.started_at
    logger.info(
        "ingestion-end universe=%d status=%d perf=%d mv=%d transfers=%d failed=%d requests=%d "
        "stopped_by=%s duration=%.0fs",
        stats.universe_size,
        stats.status_written,
        stats.performance_fetched,
        stats.mv_fetched,
        stats.transfers_fetched,
        stats.failed,
        stats.requests,
        stats.stopped_by,
        stats.duration_s,
    )
    return stats
