"""`predictions` writes and the bulk reads the store scorer needs (PR E Task 1)."""

from __future__ import annotations

from datetime import date

from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore


def _perf(matches):
    return {"it": [{"ti": "2026/2027", "ph": matches}]}


def _match(day, md, st, p, mp="90"):
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


def _seed(dsn):
    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [
            {
                "player_id": "a",
                "first_name": None,
                "last_name": "A",
                "position": "Midfielder",
                "team_id": "1",
                "market_value": 5_000_000,
                "average_points": 40.0,
            },
            {
                "player_id": "b",
                "first_name": None,
                "last_name": "B",
                "position": "Forward",
                "team_id": "2",
                "market_value": 3_000_000,
                "average_points": 20.0,
            },
            {
                "player_id": "nopos",
                "first_name": None,
                "last_name": "N",
                "position": None,
                "team_id": "2",
                "market_value": 1,
                "average_points": 0.0,
            },
        ]
    )
    corpus.record_match_history(
        "a",
        "1",
        _perf(
            [
                _match(1, "2026-08-22T13:30:00Z", 5, 80),
                _match(2, "2026-08-29T13:30:00Z", 4, 0),
                _match(4, "2026-09-19T13:30:00Z", 0, 0),
            ]
        ),
    )
    corpus.record_match_history("b", "2", _perf([_match(1, "2026-08-22T13:30:00Z", 3, 12)]))
    corpus.record_status_daily(
        "a",
        date(2026, 9, 15),
        {"st": 0, "prob": 1, "mv": 5_000_000, "tid": "1"},
        1_789_500_000.0,
    )
    corpus.record_status_daily(
        "a",
        date(2026, 9, 14),
        {"st": 2, "prob": 2, "mv": 5_000_000, "tid": "1"},
        1_789_400_000.0,
    )
    return corpus


def test_current_season_is_the_newest_title(store_dsn):
    _seed(store_dsn)
    assert CalibrationStore(dsn=store_dsn).current_season() == "2026/2027"


def test_stored_players_assembles_rows_per_player(store_dsn):
    _seed(store_dsn)
    players = {
        p.player_id: p
        for p in CalibrationStore(dsn=store_dsn).stored_players(
            since_iso="2026-08-01T00:00:00Z", status_day=date(2026, 9, 15)
        )
    }
    assert set(players) == {"a", "b"}  # no position → not a scorable player
    a = players["a"]
    assert (a.position, a.team_id, a.live_status, a.lineup_probability) == (
        "Midfielder",
        "1",
        0,
        1,
    )
    assert a.status_fetched_at == 1_789_500_000.0  # the newest of the two days
    assert [m["day_number"] for m in a.matches] == [1, 2, 4]
    assert a.matches[0]["status"] == 5 and a.matches[0]["points"] == 80
    b = players["b"]
    assert b.live_status is None and b.status_fetched_at is None


def test_stored_players_before_is_the_leak_boundary(store_dsn):
    _seed(store_dsn)
    players = {
        p.player_id: p
        for p in CalibrationStore(dsn=store_dsn).stored_players(
            since_iso="2026-08-01T00:00:00Z",
            status_day=date(2026, 9, 15),
            before_iso="2026-08-29T13:30:00Z",
        )
    }
    assert [m["day_number"] for m in players["a"].matches] == [1]


def test_write_predictions_upserts_on_session_and_player(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    row = {
        "session_id": "s1",
        "player_id": "a",
        "season": "2026/2027",
        "day_number": 4,
        "kickoff": 1_789_756_200.0,
        "predicted_at": 1_789_500_000.0,
        "predicted_ep": 41.5,
        "p_status": {1: 0.1, 3: 0.2, 4: 0.1, 5: 0.6},
        "rate": 60.0,
        "prev_status": 5,
        "live_status": 0,
        "position": "Midfielder",
        "team_id": "1",
        "owned": True,
        "listed": False,
        "in_best_11": True,
        "live_ep": 44.0,
        "data_grade": "A",
        "app": "cli",
        "dry_run": True,
        "backfill": False,
    }
    assert store.write_predictions([row]) == 1
    assert store.write_predictions([dict(row, predicted_ep=50.0)]) == 1
    with store.connection() as conn:
        got = conn.execute(
            "SELECT predicted_ep, p_status, owned FROM rehoboam.predictions "
            "WHERE session_id = 's1' AND player_id = 'a'"
        ).fetchone()
    assert got["predicted_ep"] == 50.0 and got["p_status"] == {
        "5": 0.6,
        "1": 0.1,
        "3": 0.2,
        "4": 0.1,
    }
    assert got["owned"] is True


from rehoboam.services.calibration import CalRow, build_report  # noqa: E402
from rehoboam.services.session_facts import IntegrityFailure, SessionFacts  # noqa: E402
from rehoboam.store.session_store import SessionStore  # noqa: E402

KICKOFF = 1_789_756_200.0  # 2026-09-18 18:30 UTC


def _pred(session, pid, at, **over):
    row = {
        "session_id": session,
        "player_id": pid,
        "season": "2026/2027",
        "day_number": 4,
        "kickoff": KICKOFF,
        "predicted_at": at,
        "predicted_ep": 40.0,
        "p_status": {1: 0.1, 3: 0.2, 4: 0.1, 5: 0.6},
        "rate": 60.0,
        "prev_status": 5,
        "live_status": 0,
        "position": "Midfielder",
        "team_id": "1",
        "owned": False,
        "listed": False,
        "in_best_11": False,
        "live_ep": None,
        "data_grade": "A",
        "app": "function",
        "dry_run": False,
        "backfill": False,
    }
    row.update(over)
    return row


def test_last_prediction_before_kickoff_per_player(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    store.write_predictions(
        [
            _pred("s1", "a", KICKOFF - 7200, predicted_ep=30.0),
            _pred("s2", "a", KICKOFF - 60, predicted_ep=35.0),
            _pred("s3", "a", KICKOFF + 60, predicted_ep=99.0),  # after kickoff: ignored
            _pred("bf", "a", KICKOFF - 1, predicted_ep=11.0, backfill=True),
            _pred("s2", "b", KICKOFF - 60, predicted_ep=20.0, day_number=5),  # other matchday
        ]
    )
    got = store.last_predictions_before(
        season="2026/2027", day_number=4, kickoff=KICKOFF, backfill=False
    )
    assert set(got) == {"a"} and got["a"]["predicted_ep"] == 35.0 and got["a"]["session_id"] == "s2"
    bf = store.last_predictions_before(
        season="2026/2027", day_number=4, kickoff=KICKOFF, backfill=True
    )
    assert bf["a"]["predicted_ep"] == 11.0


def test_squad_before_comes_from_the_last_real_session(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    store.write_predictions(
        [
            _pred("dry", "a", KICKOFF - 30, dry_run=True, owned=True, in_best_11=True),
            _pred("real", "a", KICKOFF - 60, owned=True, in_best_11=True),
            _pred("real", "b", KICKOFF - 60, owned=True, in_best_11=False),
            _pred("older", "c", KICKOFF - 7200, owned=True, in_best_11=True),
        ]
    )
    assert store.squad_before(season="2026/2027", day_number=4, kickoff=KICKOFF) == (
        {"a", "b"},
        {"a"},
    )


def test_actuals_join_the_universe(store_dsn):
    _seed(store_dsn)
    store = CalibrationStore(dsn=store_dsn)
    rows = store.actuals_for(season="2026/2027", day_number=1)
    assert {r["player_id"] for r in rows} == {"a", "b"}
    a = next(r for r in rows if r["player_id"] == "a")
    assert (a["points"], a["status"], a["position"], a["name"]) == (
        80,
        5,
        "Midfielder",
        "A",
    )


def test_history_before_is_strict(store_dsn):
    corpus = _seed(store_dsn)
    corpus.record_match_history("c", "3", _perf([_match(1, "2026-08-22T13:30:00Z", 5, 99)]))
    hist = CalibrationStore(dsn=store_dsn).history_before(
        before_iso="2026-08-29T13:30:00Z", player_ids=["a", "b"]
    )
    assert [m["day_number"] for m in hist["a"]] == [1] and hist["b"][0]["points"] == 12
    assert "c" not in hist  # not in player_ids: excluded even though he has history


def test_players_needing_final_rows(store_dsn):
    corpus = _seed(store_dsn)
    corpus.record_status_daily(
        "b", date(2026, 9, 15), {"st": 0, "prob": 1, "mv": 3_000_000, "tid": "2"}, 1_789_500_000.0
    )
    corpus.mark_fetched("a", at=1_000.0, performance=True)  # before the whistle
    corpus.mark_fetched("b", at=9_999.0, performance=True)  # after
    store = CalibrationStore(dsn=store_dsn)
    assert store.players_needing_final_rows(
        season="2026/2027", day_number=1, whistle=5_000.0, live_since=0.0
    ) == ["a"]
    assert (
        store.players_needing_final_rows(
            season="2026/2027", day_number=1, whistle=500.0, live_since=0.0
        )
        == []
    )
    # live_since above both players' status fetch stamps: neither is in the live universe.
    assert (
        store.players_needing_final_rows(
            season="2026/2027", day_number=1, whistle=5_000.0, live_since=1_789_500_000.0 + 1
        )
        == []
    )
    # live_since=None: every pre-whistle player, regardless of status rows -- even
    # one with none at all.
    with store.connection() as conn:
        conn.execute("DELETE FROM rehoboam.player_status_daily WHERE player_id = 'a'")
    assert store.players_needing_final_rows(season="2026/2027", day_number=1, whistle=5_000.0) == [
        "a"
    ]


def test_write_and_read_a_report(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    rows = [
        CalRow("a", "Midfielder", 50.0, 40.0, 30.0, 44.0, True, True, 0),
        CalRow("b", "Forward", 0.0, None, 10.0, None, False, False, None),
    ]
    report = build_report(rows, n_stale_rows=2)
    gate = {"passes": False, "consecutive_ok": 0}
    store.write_calibration(
        season="2026/2027",
        day_number=4,
        backfill=False,
        rows=[
            {
                "player_id": "a",
                "session_id": "s2",
                "predicted_ep": 40.0,
                "live_ep": 44.0,
                "baseline_ep": 30.0,
                "actual_points": 50,
                "minutes": 90,
                "status": 5,
                "position": "Midfielder",
                "team_id": "1",
                "owned": True,
                "in_best_11": True,
                "prev_status": 5,
                "live_status": 0,
            },
            {
                "player_id": "b",
                "session_id": None,
                "predicted_ep": None,
                "live_ep": None,
                "baseline_ep": 10.0,
                "actual_points": 0,
                "minutes": 0,
                "status": 1,
                "position": "Forward",
                "team_id": "2",
                "owned": False,
                "in_best_11": False,
                "prev_status": None,
                "live_status": None,
            },
        ],
        report=report,
        gate=gate,
        computed_at=123.0,
    )
    got = store.report_for("2026/2027", 4)
    assert got["n"] == 1 and got["n_unpredicted"] == 1 and got["n_stale_rows"] == 2
    assert got["gate"] == gate and got["telegram_sent"] is False
    assert got["by_position"]["Midfielder"]["n"] == 1
    assert store.report_for("2026/2027", 4, backfill=True) is None
    store.mark_telegram_sent("2026/2027", 4)
    assert store.report_for("2026/2027", 4)["telegram_sent"] is True
    # Re-writing the same matchday replaces, never duplicates.
    store.write_calibration(
        season="2026/2027",
        day_number=4,
        backfill=False,
        rows=[],
        report=build_report([]),
        gate=gate,
        computed_at=124.0,
    )
    assert store.report_for("2026/2027", 4)["n"] == 0
    with store.connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM rehoboam.calibration_rows").fetchone()["n"]
    assert n == 0


def test_recent_reports_are_oldest_first_and_real_only(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    for day, bf in ((5, False), (4, False), (3, True)):
        store.write_calibration(
            season="2026/2027",
            day_number=day,
            backfill=bf,
            rows=[],
            report=build_report([]),
            gate=None,
            computed_at=1.0,
        )
    assert [r["day_number"] for r in store.recent_reports("2026/2027")] == [4, 5]


def test_delete_report_clears_rows_and_report_leaving_the_real_one(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    for bf in (False, True):
        store.write_calibration(
            season="2026/2027",
            day_number=4,
            backfill=bf,
            rows=[
                {
                    "player_id": "a",
                    "session_id": "s1",
                    "predicted_ep": 10.0,
                    "live_ep": None,
                    "baseline_ep": 5.0,
                    "actual_points": 10,
                    "minutes": 90,
                    "status": 5,
                    "position": "Midfielder",
                    "team_id": "1",
                    "owned": False,
                    "in_best_11": False,
                    "prev_status": None,
                    "live_status": None,
                }
            ],
            report=build_report(
                [CalRow("a", "Midfielder", 10.0, 10.0, 5.0, None, False, False, 5)]
            ),
            gate=None,
            computed_at=1.0,
        )
    store.delete_report("2026/2027", 4, backfill=True)
    assert store.report_for("2026/2027", 4, backfill=True) is None
    assert store.report_for("2026/2027", 4) is not None
    with store.connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM rehoboam.calibration_rows "
            "WHERE season = %s AND day_number = 4 AND backfill = true",
            ("2026/2027",),
        ).fetchone()["n"]
    assert n == 0


def test_last_integrity_failure_ignores_dry_runs(store_dsn):
    sessions = SessionStore(dsn=store_dsn)
    sessions.record(
        SessionFacts(
            session_id="d",
            app="cli",
            mode="full",
            dry_run=True,
            started_at=1.0,
            duration_s=1.0,
        )
    )
    sessions.record(
        SessionFacts(
            session_id="r",
            app="function",
            mode="lineup_only",
            dry_run=False,
            started_at=1.0,
            duration_s=1.0,
        )
    )
    sessions.record_failures("d", [IntegrityFailure("I2", "x")], at=900.0)
    store = CalibrationStore(dsn=store_dsn)
    assert store.last_integrity_failure_at() is None
    sessions.record_failures("r", [IntegrityFailure("I2", "x")], at=800.0)
    assert store.last_integrity_failure_at() == 800.0
