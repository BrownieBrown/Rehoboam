"""Base XI's table as a view over the store (G1 Task 3)."""

from __future__ import annotations

import time
from datetime import date

from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore

NOW = time.time()


def _perf(seasons):
    return {"it": [{"ti": title, "ph": matches} for title, matches in seasons]}


def _m(day, st, p, md="2026-08-22T13:30:00Z"):
    return {
        "day": day,
        "md": md,
        "st": st,
        "p": p,
        "mp": "90",
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
                "last_name": "Alpha",
                "position": "Midfielder",
                "team_id": "7",
                "market_value": 10_000_000,
                "average_points": 50.0,
            },
            {
                "player_id": "b",
                "first_name": None,
                "last_name": "Beta",
                "position": "Midfielder",
                "team_id": "8",
                "market_value": 2_000_000,
                "average_points": 10.0,
            },
        ]
    )
    corpus.record_match_history(
        "a",
        "7",
        _perf(
            [
                (
                    "2025/2026",
                    [
                        _m(1, 5, 100, "2025-08-23T13:30:00Z"),
                        _m(2, 4, 0, "2025-08-30T13:30:00Z"),
                    ],
                ),
                (
                    "2026/2027",
                    [
                        _m(1, 5, 80),
                        _m(2, 3, 20, "2026-08-29T13:30:00Z"),
                        _m(3, 1, 0, "2026-09-12T13:30:00Z"),
                        _m(4, 0, 0, "2026-09-19T13:30:00Z"),
                    ],
                ),
            ]
        ),
    )
    corpus.record_match_history("b", "8", _perf([("2026/2027", [_m(1, 5, 10)])]))
    corpus.record_status_daily(
        "a", date.today(), {"st": 0, "prob": 1, "mv": 12_000_000, "tid": "7"}, NOW
    )
    league = LeagueStore(dsn=dsn)
    league.upsert_teams(
        [{"team_id": "7", "name": "Club Seven", "short_name": "SEV", "updated_at": NOW}]
    )
    league.upsert_managers(
        [
            {
                "manager_id": "m2",
                "league_id": "L",
                "name": "Rival",
                "is_self": False,
                "updated_at": NOW,
            }
        ]
    )
    league.write_squads(
        [
            {
                "snapshot_at": NOW,
                "manager_id": "m2",
                "player_id": "a",
                "market_value": 12_000_000,
                "gain_loss": None,
                "on_market": None,
                "source": "ingest",
            }
        ]
    )
    with corpus.connection() as conn:
        conn.execute(
            "INSERT INTO rehoboam.mv_series (player_id, snapshot_at, market_value) VALUES "
            "('a', %s, 10000000), ('a', %s, 11000000)",
            (NOW - 8 * 86400, NOW - 86400 - 60),
        )
    CalibrationStore(dsn=dsn).write_predictions(
        [
            {
                "session_id": "s",
                "player_id": "a",
                "season": "2026/2027",
                "day_number": 4,
                "kickoff": NOW + 86400,
                "predicted_at": NOW - 100,
                "predicted_ep": 61.5,
                "p_status": {1: 0.05, 3: 0.15, 4: 0.1, 5: 0.7},
                "rate": 80.0,
                "prev_status": 5,
                "live_status": 0,
                "position": "Midfielder",
                "team_id": "7",
                "owned": False,
                "listed": False,
                "in_best_11": False,
                "live_ep": None,
                "data_grade": "A",
                "app": "cli",
                "dry_run": True,
                "backfill": False,
            }
        ]
    )
    return league


def test_the_view_has_base_xi_columns_in_order(store_dsn):
    _seed(store_dsn)
    with LeagueStore(dsn=store_dsn).connection() as conn:
        cols = [
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='rehoboam' AND table_name='player_table' ORDER BY ordinal_position"
            ).fetchall()
        ]
    assert cols == [
        "player_id",
        "name",
        "team",
        "position",
        "market_value",
        "trend_24h_pct",
        "trend_7d_pct",
        "points",
        "avg_points",
        "median_points",
        "points_per_million",
        "points_prev",
        "avg_points_prev",
        "appearances",
        "appearances_prev",
        "starts",
        "starts_prev",
        "owner",
        "predicted_ep",
        "p_start",
        "fair_value_gap",
    ]


def test_the_numbers(store_dsn):
    league = _seed(store_dsn)
    rows = {r["player_id"]: r for r in league.player_table()}
    a, b = rows["a"], rows["b"]
    assert a["team"] == "Club Seven" and a["market_value"] == 12_000_000
    assert (
        a["points"] == 100 and a["appearances"] == 2 and a["starts"] == 1
    )  # statuses 5, 3; not 1 or 0
    assert float(a["avg_points"]) == 50.0 and float(a["median_points"]) == 50.0
    assert a["points_prev"] == 100 and a["appearances_prev"] == 1 and a["starts_prev"] == 1
    assert round(float(a["points_per_million"]), 2) == round(100 / 12.0, 2)
    assert round(float(a["trend_24h_pct"]), 1) == round(100 * (12 - 11) / 11, 1)
    assert round(float(a["trend_7d_pct"]), 1) == round(100 * (12 - 10) / 10, 1)
    assert a["owner"] == "Rival" and float(a["predicted_ep"]) == 61.5 and float(a["p_start"]) == 0.7
    assert b["owner"] == "Kickbase" and b["team"] is None and b["points_prev"] is None
    assert b["predicted_ep"] is None and b["trend_24h_pct"] is None


def test_filters_and_order(store_dsn):
    league = _seed(store_dsn)
    assert [r["player_id"] for r in league.player_table(owner="Rival")] == ["a"]
    assert [
        r["player_id"] for r in league.player_table(position="Midfielder", order_by="points")
    ] == ["a", "b"]
