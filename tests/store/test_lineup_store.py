"""Predicted elevens in the store, and the fit's join of lineup code to played status."""

from __future__ import annotations

from rehoboam.scoring.v2.lineup_prob import OUTSIDE_ALTERNATIVE, OUTSIDE_CONTESTED
from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore
from rehoboam.store.lineup_store import COVER_SLOT_BASE, PredictedLineupStore

SEASON = "2026/2027"
KICKOFF_MD5 = 1_760_000_000.0  # any epoch; only its UTC date matters to the join
import datetime as _dt  # noqa: E402

UTC_DAY_MD5 = _dt.datetime.fromtimestamp(KICKOFF_MD5, tz=_dt.timezone.utc).date()


def _perf(matches):
    return {"it": [{"ti": SEASON, "ph": matches}]}


def _match(day, md, st, p):
    return {"day": day, "md": md, "st": st, "p": p, "mp": "90", "t1": "1", "t2": "2", "pt": "1"}


def _seed(dsn):
    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [
            {
                "player_id": "a",
                "first_name": "",
                "last_name": "A",
                "position": "Forward",
                "team_id": "2",
            },
            {
                "player_id": "b",
                "first_name": "",
                "last_name": "B",
                "position": "Forward",
                "team_id": "2",
            },
            {
                "player_id": "c",
                "first_name": "",
                "last_name": "C",
                "position": "Forward",
                "team_id": "9",
            },
        ]
    )
    league = LeagueStore(dsn=dsn)
    league.upsert_fixtures(
        [
            {
                "match_id": "m5",
                "season": SEASON,
                "day_number": 5,
                "kickoff": KICKOFF_MD5,
                "home_team_id": "2",
                "away_team_id": "9",
                "home_goals": None,
                "away_goals": None,
                "status": 0,
                "updated_at": 1.0,
            },
            {
                "match_id": "m5b",
                "season": SEASON,
                "day_number": 5,
                "kickoff": KICKOFF_MD5 + 2 * 86400,  # the Sunday game
                "home_team_id": "9",
                "away_team_id": "2",
                "home_goals": None,
                "away_goals": None,
                "status": 0,
                "updated_at": 1.0,
            },
            {
                "match_id": "m6",
                "season": SEASON,
                "day_number": 6,
                "kickoff": KICKOFF_MD5 + 7 * 86400,
                "home_team_id": "9",
                "away_team_id": "2",
                "home_goals": None,
                "away_goals": None,
                "status": 0,
                "updated_at": 1.0,
            },
        ]
    )
    return corpus, league


def _rows(*names, in_xi=True, fetched_at=10.0):
    return [
        {
            "source": "ligainsider",
            "season": SEASON,
            "day_number": 5,
            "team_id": "2",
            "player_name": n,
            "player_id": pid,
            "in_xi": in_xi,
            "slot": i + 1,
            "source_updated_at": "05.10.2026 14:12",
            "fetched_at": fetched_at,
        }
        for i, (n, pid) in enumerate(names)
    ]


def test_write_upserts_on_the_name_key_and_the_newest_fetch_wins(store_dsn):
    _seed(store_dsn)
    store = PredictedLineupStore(dsn=store_dsn)
    assert store.write(_rows(("A", "a"), ("B", None))) == 2
    assert store.write(_rows(("B", "b"), in_xi=False, fetched_at=20.0)) == 1
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT player_name, player_id, in_xi, fetched_at FROM rehoboam.predicted_lineups "
            "ORDER BY player_name"
        ).fetchall()
    assert [(r["player_name"], r["player_id"], r["in_xi"], r["fetched_at"]) for r in rows] == [
        ("A", "a", True, 10.0),
        ("B", "b", False, 20.0),
    ]
    assert store.coverage(season=SEASON, day_number=5) == {"2": 20.0}


def test_xi_for_judges_every_member_of_a_predicted_club_and_nobody_else(store_dsn):
    _seed(store_dsn)
    store = PredictedLineupStore(dsn=store_dsn)
    store.write(_rows(("A", "a")))
    xi = store.xi_for(season=SEASON, day_number=5)
    assert xi == {"a": True, "b": False}  # c plays for a club without a prediction
    assert store.xi_for(season=SEASON, day_number=6) == {}


def test_upcoming_matchday_is_the_first_not_fully_finished(store_dsn):
    _seed(store_dsn)
    store = PredictedLineupStore(dsn=store_dsn)
    md = store.upcoming_matchday(KICKOFF_MD5 - 86400)
    assert (md["season"], md["day_number"], md["first_kickoff"]) == (SEASON, 5, KICKOFF_MD5)
    assert md["last_kickoff"] == KICKOFF_MD5 + 2 * 86400
    # between the Friday and the Sunday game it is still the upcoming one …
    assert store.upcoming_matchday(KICKOFF_MD5 + 86400)["day_number"] == 5
    # … and after its last kickoff the next one is
    assert store.upcoming_matchday(KICKOFF_MD5 + 2 * 86400 + 3600)["day_number"] == 6
    assert store.upcoming_matchday(KICKOFF_MD5 + 30 * 86400) is None


def test_lineup_code_rows_join_the_day_before_reading_and_the_outside_verdict(store_dsn):
    corpus, _ = _seed(store_dsn)
    day_before = UTC_DAY_MD5 - _dt.timedelta(days=1)
    stale = UTC_DAY_MD5 - _dt.timedelta(days=5)
    corpus.record_status_daily("a", day_before, {"st": 0, "prob": 1}, KICKOFF_MD5 - 3600)
    corpus.record_status_daily("b", day_before, {"st": 0, "prob": 3}, KICKOFF_MD5 - 3600)
    corpus.record_status_daily("c", stale, {"st": 0, "prob": 5}, KICKOFF_MD5 - 5 * 86400)
    # a same-day reading must not leak in: it may already know the lineup
    corpus.record_status_daily("a", UTC_DAY_MD5, {"st": 0, "prob": 5}, KICKOFF_MD5 - 60)
    for pid, st, pts in (("a", 5, 120), ("b", 4, 0), ("c", 1, 0)):
        corpus.record_match_history(pid, "2", _perf([_match(5, "2026-10-09T18:30:00Z", st, pts)]))
    corpus.record_match_history("a", "2", _perf([_match(6, "2026-10-16T18:30:00Z", 0, 0)]))
    PredictedLineupStore(dsn=store_dsn).write(_rows(("A", "a")))

    rows = CalibrationStore(dsn=store_dsn).lineup_code_rows(
        season=SEASON, before=KICKOFF_MD5 + 1, max_age_days=3
    )
    by_id = {r["player_id"]: r for r in rows}
    assert set(by_id) == {"a", "b"}  # c's reading is older than three days
    assert (by_id["a"]["lineup_probability"], by_id["a"]["status"], by_id["a"]["points"]) == (
        1,
        5,
        120,
    )
    assert by_id["a"]["status_day"] == day_before
    assert by_id["a"]["predicted_xi"] is True
    assert by_id["b"]["predicted_xi"] is False  # his club was predicted without him
    assert by_id["b"]["lineup_probability"] == 3
    # a matchday that has not kicked off yet contributes nothing
    assert (
        CalibrationStore(dsn=store_dsn).lineup_code_rows(season=SEASON, before=KICKOFF_MD5 - 1)
        == []
    )


def test_codes_for_reads_the_linked_alternative_and_the_missing(store_dsn):
    _seed(store_dsn)
    store = PredictedLineupStore(dsn=store_dsn)
    store.write(
        _rows(("A", "a"))
        + [
            dict(_rows(("B", "b"))[0], in_xi=False, slot=COVER_SLOT_BASE + 1),
            dict(_rows(("Gone", None))[0], in_xi=False, slot=None),
        ]
    )
    assert store.codes_for(season=SEASON, day_number=5) == {
        "a": OUTSIDE_CONTESTED,  # b is listed beside him
        "b": OUTSIDE_ALTERNATIVE,
    }
    assert store.xi_for(season=SEASON, day_number=5) == {"a": True, "b": False}


def test_team_kickoffs_are_each_clubs_own_first_whistle(store_dsn):
    _seed(store_dsn)
    kickoffs = PredictedLineupStore(dsn=store_dsn).team_kickoffs(season=SEASON, day_number=5)
    assert kickoffs == {"2": KICKOFF_MD5, "9": KICKOFF_MD5}


def _prediction(session, pid, predicted_at, xi, *, dry_run=False):
    return {
        "session_id": session,
        "player_id": pid,
        "season": SEASON,
        "day_number": 5,
        "kickoff": KICKOFF_MD5,
        "predicted_at": predicted_at,
        "predicted_ep": 40.0,
        "p_status": {1: 0.1, 3: 0.2, 4: 0.1, 5: 0.6},
        "rate": 60.0,
        "prev_status": 5,
        "live_status": 0,
        "position": "Forward",
        "team_id": "2",
        "owned": False,
        "listed": False,
        "in_best_11": False,
        "live_ep": None,
        "data_grade": "A",
        "app": "function",
        "dry_run": dry_run,
        "backfill": False,
        "predicted_xi": xi,
    }


def test_outside_xi_by_lead_keeps_monday_and_friday_verdicts_apart(store_dsn):
    corpus, _ = _seed(store_dsn)
    for pid, st in (("a", 5), ("b", 4)):
        corpus.record_match_history(pid, "2", _perf([_match(5, "2026-10-09T18:30:00Z", st, 0)]))
    store = CalibrationStore(dsn=store_dsn)
    four_days, half_day = KICKOFF_MD5 - 4 * 86400 + 60, KICKOFF_MD5 - 43200
    store.write_predictions(
        [
            _prediction("mon", "a", four_days, False),  # Monday: left out, then started
            _prediction("mon", "b", four_days, True),
            _prediction("fri1", "a", half_day - 3600, False),
            _prediction("fri2", "a", half_day, True),  # the newest same-day session wins
            _prediction("fri2", "b", half_day, False),
            _prediction("dry", "b", half_day, True, dry_run=True),  # a status run: ignored
        ]
    )
    rows = store.outside_xi_by_lead(season=SEASON, before=KICKOFF_MD5 + 1)
    got = {(r["lead_days"], r["predicted_xi"]): (r["n"], r["started"]) for r in rows}
    assert got == {
        (0, False): (1, 0),
        (0, True): (1, 1),
        (3, False): (1, 1),
        (3, True): (1, 0),
    }
