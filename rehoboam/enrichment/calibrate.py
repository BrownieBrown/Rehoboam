"""Finished matchdays → calibration rows → one report, on the ingestion app (PR E §2).

Two entry points around the ingest loop: `prepare_refresh` before it (clear
the fetch stamps of players whose rows predate the final whistle, so the loop
re-reads them), `run_calibration` after it (report every finished matchday
whose rows are final). `backfill_predictions` writes leak-free predictions
for a matchday that finished before this code existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from rehoboam.backtest.baselines import season_average_baseline
from rehoboam.kickoff import FinishedMatchday, finished_matchdays
from rehoboam.notify.telegram import send_message
from rehoboam.scoring.store_scorer import StoredPlayer, score_stored
from rehoboam.scoring.v2.coefficients import load_coefficients
from rehoboam.services.calibration import (
    CalibrationReport,
    CalRow,
    build_report,
    gate_verdict,
    render_calibration_message,
)
from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore

logger = logging.getLogger(__name__)

WHISTLE_AFTER_KICKOFF_S = 3 * 3600
MAX_WAIT_FOR_ROWS_S = 72 * 3600


@dataclass
class CalibrationOutcome:
    reported: list[int] = field(default_factory=list)
    waiting: dict[int, int] = field(default_factory=dict)
    settled: list[int] = field(default_factory=list)
    error: str | None = None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _whistle(md: FinishedMatchday) -> float:
    return md.last_kickoff.timestamp() + WHISTLE_AFTER_KICKOFF_S


def _unreported(
    store: CalibrationStore,
    schedule,
    *,
    season: str,
    now: float,
    backfill: bool,
    only_day: int | None,
) -> list[FinishedMatchday]:
    existing = {r["day_number"] for r in store.recent_reports(season, backfill=backfill)}
    out = []
    for md in finished_matchdays(schedule):
        if only_day is not None and md.day_number != only_day:
            continue
        if now < _whistle(md):
            continue
        if md.day_number in existing:
            continue
        out.append(md)
    return out


def prepare_refresh(
    store: CalibrationStore, corpus: CorpusStore, schedule, *, season: str, now: float
) -> dict[int, int]:
    """Before the ingest loop: make the loop re-read every player whose rows
    for a finished, unreported matchday predate the whistle. Returns
    `{day_number: players cleared}`."""
    cleared: dict[int, int] = {}
    for md in _unreported(store, schedule, season=season, now=now, backfill=False, only_day=None):
        blocking = store.players_needing_final_rows(
            season=season,
            day_number=md.day_number,
            whistle=_whistle(md),
            live_since=now - 48 * 3600,
        )
        if blocking:
            corpus.clear_performance_fetched(blocking)
            cleared[md.day_number] = len(blocking)
    return cleared


def _rows_for(
    store: CalibrationStore,
    md: FinishedMatchday,
    *,
    season: str,
    backfill: bool,
    preds: dict[str, dict],
    stale: set[str],
) -> tuple[list[CalRow], list[dict], dict[str, str]]:
    kickoff = md.first_kickoff.timestamp()
    actuals = store.actuals_for(season=season, day_number=md.day_number)
    owned_ids, fielded_ids = (
        (set(), set())
        if backfill
        else store.squad_before(season=season, day_number=md.day_number, kickoff=kickoff)
    )
    history = store.history_before(
        before_iso=_iso(kickoff), player_ids=[a["player_id"] for a in actuals]
    )
    cal_rows: list[CalRow] = []
    db_rows: list[dict] = []
    names: dict[str, str] = {}
    for a in actuals:
        pid = a["player_id"]
        if pid in stale:
            continue
        p = preds.get(pid)
        baseline = season_average_baseline(history.get(pid, []))
        names[pid] = a["name"]
        owned = pid in owned_ids
        in_best_11 = pid in fielded_ids
        cal_rows.append(
            CalRow(
                player_id=pid,
                position=a["position"],
                actual=float(a["points"]),
                predicted=float(p["predicted_ep"]) if p else None,
                baseline=float(baseline),
                live=float(p["live_ep"]) if p and p["live_ep"] is not None else None,
                owned=owned,
                in_best_11=in_best_11,
                live_status=p["live_status"] if p else None,
                played=int(a["minutes"]) > 0,
            )
        )
        db_rows.append(
            {
                "player_id": pid,
                "session_id": p["session_id"] if p else None,
                "predicted_ep": float(p["predicted_ep"]) if p else None,
                "live_ep": p["live_ep"] if p else None,
                "baseline_ep": float(baseline),
                "actual_points": int(a["points"]),
                "minutes": int(a["minutes"]),
                "status": a["status"],
                "position": a["position"],
                "team_id": a["team_id"],
                "owned": owned,
                "in_best_11": in_best_11,
                "prev_status": p["prev_status"] if p else None,
                "live_status": p["live_status"] if p else None,
            }
        )
    return cal_rows, db_rows, names


def _send(
    store: CalibrationStore,
    *,
    season: str,
    day_number: int,
    text: str,
    telegram: tuple[str, str],
) -> None:
    token, chat_id = telegram
    try:
        if send_message(token, chat_id, text):
            store.mark_telegram_sent(season, day_number)
        else:
            logger.warning("calibration: telegram send returned False for MD%d", day_number)
    except Exception:
        logger.warning("calibration: telegram send failed for MD%d", day_number, exc_info=True)


def _resend_unsent(
    store: CalibrationStore,
    *,
    season: str,
    now: float,
    telegram: tuple[str, str],
    skip: set[int],
) -> None:
    for r in store.recent_reports(season):
        if r["telegram_sent"] or r["n"] == 0 or r["day_number"] in skip:
            continue
        keys = set(CalibrationReport.__dataclass_fields__)
        report = CalibrationReport(**{k: r[k] for k in keys})
        names = {
            a["player_id"]: a["name"]
            for a in store.actuals_for(season=season, day_number=r["day_number"])
        }
        text = render_calibration_message(
            report,
            season=season,
            day_number=r["day_number"],
            names=names,
            gate=r["gate"] or {},
        )
        _send(
            store,
            season=season,
            day_number=r["day_number"],
            text=text,
            telegram=telegram,
        )


def run_calibration(
    store: CalibrationStore,
    schedule,
    *,
    season: str,
    now: float,
    backfill: bool = False,
    only_day: int | None = None,
    telegram: tuple[str, str] | None = None,
    max_wait_s: float = MAX_WAIT_FOR_ROWS_S,
) -> CalibrationOutcome:
    """Report every finished matchday whose rows are final (or overdue)."""
    outcome = CalibrationOutcome()
    try:
        for md in _unreported(
            store,
            schedule,
            season=season,
            now=now,
            backfill=backfill,
            only_day=only_day,
        ):
            whistle = _whistle(md)
            # `stale`: every pre-whistle row — excluded from the report and counted,
            # whether or not the player can ever be refetched. `blocking`: the subset
            # still in the live universe — only these are worth waiting for or clearing.
            stale = store.players_needing_final_rows(
                season=season, day_number=md.day_number, whistle=whistle
            )
            blocking = store.players_needing_final_rows(
                season=season,
                day_number=md.day_number,
                whistle=whistle,
                live_since=now - 48 * 3600,
            )
            if blocking and now < whistle + max_wait_s:
                outcome.waiting[md.day_number] = len(blocking)
                logger.info(
                    "calibration: MD%d waiting for %d final rows",
                    md.day_number,
                    len(blocking),
                )
                continue
            preds = store.last_predictions_before(
                season=season,
                day_number=md.day_number,
                kickoff=md.first_kickoff.timestamp(),
                backfill=backfill,
            )
            if not preds:
                # Finished before this code existed (or the sessions never ran):
                # record an empty report so the matchday is settled once, never
                # send it, and keep it out of the gate (item 3 of the review).
                store.write_calibration(
                    season=season,
                    day_number=md.day_number,
                    backfill=backfill,
                    rows=[],
                    report=build_report([]),
                    gate=None,
                    computed_at=now,
                    telegram_sent=True,
                )
                outcome.settled.append(md.day_number)
                logger.info(
                    "calibration: MD%d has no predictions before kickoff; settled empty",
                    md.day_number,
                )
                continue
            cal_rows, db_rows, names = _rows_for(
                store, md, season=season, backfill=backfill, preds=preds, stale=set(stale)
            )
            report = build_report(cal_rows, n_stale_rows=len(stale))
            gate = None
            if not backfill:
                keys = set(CalibrationReport.__dataclass_fields__)
                earlier = [
                    CalibrationReport(**{k: r[k] for k in keys})
                    for r in store.recent_reports(season)
                    if r["day_number"] < md.day_number and r["n"] > 0
                ]
                gate = gate_verdict(
                    earlier + [report],
                    last_failure_at=store.last_integrity_failure_at(),
                    now=now,
                )
            store.write_calibration(
                season=season,
                day_number=md.day_number,
                backfill=backfill,
                rows=db_rows,
                report=report,
                gate=gate,
                computed_at=now,
            )
            outcome.reported.append(md.day_number)
            logger.info(
                "calibration-report md=%d n=%d spearman=%s baseline=%s regret=%s backfill=%s",
                md.day_number,
                report.n,
                report.spearman,
                report.baseline_spearman,
                report.top11_regret,
                backfill,
            )
            if telegram and not backfill:
                text = render_calibration_message(
                    report,
                    season=season,
                    day_number=md.day_number,
                    names=names,
                    gate=gate or {},
                )
                _send(
                    store,
                    season=season,
                    day_number=md.day_number,
                    text=text,
                    telegram=telegram,
                )
        if telegram and not backfill:
            _resend_unsent(
                store,
                season=season,
                now=now,
                telegram=telegram,
                skip=set(outcome.reported),
            )
    except Exception as e:
        logger.exception("calibration failed")
        outcome.error = f"{type(e).__name__}: {e}"[:500]
        if telegram:
            try:
                token, chat_id = telegram
                send_message(token, chat_id, f"Rehoboam calibration failed: {outcome.error}")
            except Exception:
                logger.warning("calibration: failure notification failed to send", exc_info=True)
    return outcome


def backfill_predictions(
    store: CalibrationStore,
    schedule,
    *,
    season: str,
    day_number: int,
    now: float,
    max_status_age_days: float,
) -> int:
    """Leak-free predictions for a finished matchday, flagged `backfill`.

    Rows dated at or after the matchday's first kickoff never reach the
    scorer; there is no live status, because `player_status_daily` did not
    exist yet — the prediction says so with `live_status = NULL`.
    """
    md = next((m for m in finished_matchdays(schedule) if m.day_number == day_number), None)
    if md is None:
        raise ValueError(f"matchday {day_number} is not finished in the schedule")
    kickoff = md.first_kickoff.timestamp()
    availability, rate, _meta = load_coefficients()
    # 400 days: enough history for prev_status (60-day window) and the played-share prior.
    since_iso = _iso(kickoff - 400 * 86400)
    players = store.stored_players(
        since_iso=since_iso,
        status_day=md.first_kickoff.date(),
        before_iso=_iso(kickoff),
    )
    actual_ids = {a["player_id"] for a in store.actuals_for(season=season, day_number=day_number)}
    rows = []
    for p in players:
        if p.player_id not in actual_ids:
            continue
        blind = StoredPlayer(
            player_id=p.player_id,
            position=p.position,
            team_id=p.team_id,
            market_value=p.market_value,
            live_status=None,
            lineup_probability=None,
            status_fetched_at=None,
            matches=p.matches,
        )
        pred = score_stored(
            blind,
            now=md.first_kickoff,
            max_status_age_days=max_status_age_days,
            availability=availability,
            rate=rate,
        )
        rows.append(
            {
                "session_id": f"backfill-md{day_number}",
                "player_id": p.player_id,
                "season": season,
                "day_number": day_number,
                "kickoff": kickoff,
                "predicted_at": kickoff - 1.0,
                "predicted_ep": pred.predicted_ep,
                "p_status": pred.p_status,
                "rate": pred.rate,
                "prev_status": pred.prev_status,
                "live_status": None,
                "position": p.position,
                "team_id": p.team_id,
                "owned": False,
                "listed": False,
                "in_best_11": False,
                "live_ep": None,
                "data_grade": pred.data_grade,
                "app": "cli",
                "dry_run": False,
                "backfill": True,
            }
        )
    return store.write_predictions(rows)
