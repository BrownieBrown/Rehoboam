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
