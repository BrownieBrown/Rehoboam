"""Finished matchdays become rows and a report; the run waits for final rows (PR E §2, Task 7)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from rehoboam.enrichment.calibrate import (
    CalibrationOutcome,
    backfill_predictions,
    prepare_refresh,
    run_calibration,
)
from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore

SEASON = "2026/2027"
MD1_FIRST = "2026-08-22T18:30:00Z"
MD1_LAST = "2026-08-23T15:30:00Z"
KICK1 = datetime(2026, 8, 22, 18, 30, tzinfo=timezone.utc).timestamp()
LAST1 = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc).timestamp()
# Status freshness is independent of how stale the performance fetch is: ingest
# refreshes status far more often than it re-confirms a final score, so a player's
# status row stays comfortably inside any test's 48h live_since window unless a
# test explicitly deletes it (simulating him leaving the live universe).
STATUS_FETCHED_AT = LAST1 + 1000 * 3600
SCHEDULE = {
    "it": [
        {"day": 1, "it": [{"dt": MD1_FIRST, "st": 2}, {"dt": MD1_LAST, "st": 2}]},
        {"day": 2, "it": [{"dt": "2026-08-29T13:30:00Z", "st": 0}]},
    ]
}


def _perf(matches):
    return {"it": [{"ti": SEASON, "ph": matches}]}


def _m(day, md, st, p, mp="90"):
    return {
        "day": day,
        "md": md,
        "st": st,
        "p": p,
        "mp": mp,
        "t1": "1",
        "t2": "2",
        "pt": "1",
    }


def _seed(dsn, *, fetched_at):
    corpus = CorpusStore(dsn=dsn)
    players = []
    pid = 0
    for pos, n in (
        ("Goalkeeper", 2),
        ("Defender", 5),
        ("Midfielder", 5),
        ("Forward", 3),
    ):
        for _ in range(n):
            pid += 1
            players.append(
                {
                    "player_id": str(pid),
                    "first_name": None,
                    "last_name": f"P{pid}",
                    "position": pos,
                    "team_id": "1",
                    "market_value": 1,
                    "average_points": 1,
                }
            )
    corpus.upsert_players(players)
    for p in players:
        i = int(p["player_id"])
        corpus.record_match_history(
            p["player_id"],
            "1",
            _perf(
                [
                    _m(1, MD1_FIRST, 5 if i % 3 else 1, 10 * i if i % 3 else 0),
                    _m(2, "2026-08-29T13:30:00Z", 0, 0),
                ]
            ),
        )
        corpus.mark_fetched(p["player_id"], at=fetched_at, performance=True)
        corpus.record_status_daily(
            p["player_id"], date(2026, 8, 23), {"st": 0, "prob": 1}, STATUS_FETCHED_AT
        )
    return corpus, [p["player_id"] for p in players]


def _predict(dsn, ids, *, at, session="s1", dry_run=False):
    store = CalibrationStore(dsn=dsn)
    store.write_predictions(
        [
            {
                "session_id": session,
                "player_id": pid,
                "season": SEASON,
                "day_number": 1,
                "kickoff": KICK1,
                "predicted_at": at,
                "predicted_ep": 5.0 * int(pid),
                "p_status": {1: 0.1, 3: 0.2, 4: 0.1, 5: 0.6},
                "rate": 60.0,
                "prev_status": 5,
                "live_status": 0,
                "position": "Midfielder",
                "team_id": "1",
                "owned": int(pid) <= 11,
                "listed": False,
                "in_best_11": int(pid) <= 11,
                "live_ep": None,
                "data_grade": "A",
                "app": "function",
                "dry_run": dry_run,
                "backfill": False,
            }
            for pid in ids
        ]
    )
    return store


def test_a_finished_matchday_with_final_rows_is_reported(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids[:-1], at=KICK1 - 60)  # one player unpredicted
    now = LAST1 + 5 * 3600
    outcome = run_calibration(store, SCHEDULE, season=SEASON, now=now)
    assert outcome == CalibrationOutcome(reported=[1], waiting={}, error=None)
    report = store.report_for(SEASON, 1)
    assert report["n"] == len(ids) - 1 and report["n_unpredicted"] == 1
    assert report["gate"]["consecutive_ok"] in (0, 1) and report["spearman"] is not None
    assert report["baseline_spearman"] is not None
    assert run_calibration(store, SCHEDULE, season=SEASON, now=now).reported == []  # idempotent


def test_waits_for_rows_fetched_before_the_whistle(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 - 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    now = LAST1 + 5 * 3600
    outcome = run_calibration(store, SCHEDULE, season=SEASON, now=now)
    assert outcome.reported == [] and outcome.waiting == {1: len(ids)}
    assert store.report_for(SEASON, 1) is None


def test_prepare_refresh_clears_the_stale_players(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 - 3600)
    corpus.mark_fetched(ids[0], at=LAST1 + 4 * 3600, performance=True)
    store = CalibrationStore(dsn=store_dsn)
    cleared = prepare_refresh(store, corpus, SCHEDULE, season=SEASON, now=LAST1 + 5 * 3600)
    assert cleared == {1: len(ids) - 1}
    assert set(corpus.players_needing_fetch("performance")) == set(ids[1:])


def test_reports_anyway_after_the_wait_cap(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 - 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    now = LAST1 + 3 * 3600 + 72 * 3600 + 1
    outcome = run_calibration(store, SCHEDULE, season=SEASON, now=now)
    assert outcome.reported == [1] and store.report_for(SEASON, 1)["n_stale_rows"] == len(ids)


def test_not_finished_or_before_the_whistle_is_skipped(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    assert run_calibration(store, SCHEDULE, season=SEASON, now=LAST1 + 60).reported == []


def test_telegram_is_sent_once_and_recorded(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=True) as send:
        run_calibration(
            store,
            SCHEDULE,
            season=SEASON,
            now=LAST1 + 5 * 3600,
            telegram=("tok", "chat"),
        )
    assert send.call_count == 1 and send.call_args.args[2].startswith("Rehoboam calibration MD1")
    assert store.report_for(SEASON, 1)["telegram_sent"] is True


def test_a_failed_send_is_retried_next_run(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=False):
        run_calibration(
            store,
            SCHEDULE,
            season=SEASON,
            now=LAST1 + 5 * 3600,
            telegram=("tok", "chat"),
        )
    assert store.report_for(SEASON, 1)["telegram_sent"] is False
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=True) as send:
        outcome = run_calibration(
            store,
            SCHEDULE,
            season=SEASON,
            now=LAST1 + 6 * 3600,
            telegram=("tok", "chat"),
        )
    assert outcome.reported == [] and send.call_count == 1
    assert store.report_for(SEASON, 1)["telegram_sent"] is True


def test_backfill_scores_before_kickoff_and_reports_apart(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = CalibrationStore(dsn=store_dsn)
    n = backfill_predictions(
        store,
        SCHEDULE,
        season=SEASON,
        day_number=1,
        now=LAST1 + 5 * 3600,
        max_status_age_days=60.0,
    )
    assert n == len(ids)
    preds = store.last_predictions_before(season=SEASON, day_number=1, kickoff=KICK1, backfill=True)
    assert set(preds) == set(ids) and preds[ids[0]]["session_id"] == "backfill-md1"
    assert preds[ids[0]]["live_status"] is None and preds[ids[0]]["prev_status"] is None
    with patch("rehoboam.enrichment.calibrate.send_message") as send:
        outcome = run_calibration(
            store,
            SCHEDULE,
            season=SEASON,
            now=LAST1 + 5 * 3600,
            backfill=True,
            only_day=1,
            telegram=("tok", "chat"),
        )
    assert outcome.reported == [1] and send.call_count == 0
    assert store.report_for(SEASON, 1, backfill=True)["n"] == len(ids)
    assert store.report_for(SEASON, 1) is None


def test_a_failed_send_is_not_retried_in_the_same_run(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=False) as send:
        run_calibration(
            store, SCHEDULE, season=SEASON, now=LAST1 + 5 * 3600, telegram=("tok", "chat")
        )
    assert send.call_count == 1


def test_a_resent_report_carries_player_names(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=False):
        run_calibration(
            store, SCHEDULE, season=SEASON, now=LAST1 + 5 * 3600, telegram=("tok", "chat")
        )
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=True) as send:
        run_calibration(
            store, SCHEDULE, season=SEASON, now=LAST1 + 6 * 3600, telegram=("tok", "chat")
        )
    text = send.call_args.args[2]
    assert "P" in text.split("worst:")[1]  # last names are P<id>, not bare ids


def test_a_finished_matchday_without_predictions_is_settled_silently(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = CalibrationStore(dsn=store_dsn)
    with patch("rehoboam.enrichment.calibrate.send_message") as send:
        outcome = run_calibration(
            store, SCHEDULE, season=SEASON, now=LAST1 + 5 * 3600, telegram=("tok", "chat")
        )
    assert outcome.settled == [1]
    assert outcome.reported == []
    assert send.call_count == 0
    report = store.report_for(SEASON, 1)
    assert report["n"] == 0
    assert report["telegram_sent"] is True
    # A second run settles nothing more and reports nothing: the matchday is done.
    second = run_calibration(
        store, SCHEDULE, season=SEASON, now=LAST1 + 6 * 3600, telegram=("tok", "chat")
    )
    assert second.reported == [] and second.settled == []


def test_a_calibration_failure_sends_one_line(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    with patch("rehoboam.enrichment.calibrate.build_report", side_effect=RuntimeError("boom")):
        with patch("rehoboam.enrichment.calibrate.send_message") as send:
            outcome = run_calibration(
                store, SCHEDULE, season=SEASON, now=LAST1 + 5 * 3600, telegram=("tok", "chat")
            )
    assert outcome.error.startswith("RuntimeError: boom")
    assert send.call_args.args[2].startswith("Rehoboam calibration failed")


def test_a_departed_player_does_not_hold_the_report_back(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    corpus.mark_fetched(ids[0], at=LAST1 - 3600, performance=True)
    with store.connection() as conn:
        conn.execute("DELETE FROM rehoboam.player_status_daily WHERE player_id = %s", (ids[0],))
    now = LAST1 + 5 * 3600
    # Without the live-universe check he would block the report until the 72h
    # cap (like test_waits_for_rows_fetched_before_the_whistle): he doesn't -- but
    # his pre-whistle row is still stale, excluded from the report, and counted.
    outcome = run_calibration(store, SCHEDULE, season=SEASON, now=now)
    assert outcome.reported == [1] and outcome.waiting == {}
    assert store.report_for(SEASON, 1)["n_stale_rows"] == 1
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT player_id FROM rehoboam.calibration_rows "
            "WHERE season = %s AND day_number = 1",
            (SEASON,),
        ).fetchall()
    assert ids[0] not in {r["player_id"] for r in rows}


def test_stale_rows_are_excluded_from_the_actuals(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 - 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    now = LAST1 + 3 * 3600 + 72 * 3600 + 1
    outcome = run_calibration(store, SCHEDULE, season=SEASON, now=now)
    assert outcome.reported == [1]
    report = store.report_for(SEASON, 1)
    assert report["n_stale_rows"] == len(ids)
    assert report["n"] == 0
    with store.connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM rehoboam.calibration_rows "
            "WHERE season = %s AND day_number = 1",
            (SEASON,),
        ).fetchone()["n"]
    assert n == 0
