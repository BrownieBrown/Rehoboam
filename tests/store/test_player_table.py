"""Base XI's table as a view over the store (G1 Task 3)."""

from __future__ import annotations

import time
from datetime import date, timedelta

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
            {
                # No day-7 player_status_daily row -- exercises the mv_series
                # fallback for trend_7d_pct. Its only status row (below) carries
                # no mv_change, so trend_24h_pct is null: that no longer falls
                # back to mv_series at all (fix round 1).
                "player_id": "c",
                "first_name": None,
                "last_name": "Gamma",
                "position": "Forward",
                "team_id": "9",
                "market_value": 12_000_000,
                "average_points": 0.0,
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
        "a",
        date.today(),
        {"st": 0, "prob": 1, "mv": 12_000_000, "tfhmvt": 1_000_000, "tid": "7"},
        NOW,
    )
    corpus.record_status_daily(
        "a",
        date.today() - timedelta(days=1),
        {"st": 0, "prob": 1, "mv": 11_000_000, "tid": "7"},
        NOW - 86400,
    )
    corpus.record_status_daily(
        "a",
        date.today() - timedelta(days=7),
        {"st": 0, "prob": 1, "mv": 10_000_000, "tid": "7"},
        NOW - 7 * 86400,
    )
    # A newest row with no mv_change (pre-migration-008-style) -- trend_24h_pct
    # must be null, not fall back to any other reading.
    corpus.record_status_daily(
        "c", date.today(), {"st": 0, "prob": 1, "mv": 12_000_000, "tid": "9"}, NOW
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
            "('c', %s, 10000000), ('c', %s, 11000000)",
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
        "fair_price",
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
    # trend_24h_pct is Kickbase's own last move (mv_change) on the newest
    # status reading (fix round 1) -- 11,000,000 -> 12,000,000 is +1,000,000.
    assert round(float(a["trend_24h_pct"]), 2) == round(100 * 1_000_000 / 11_000_000, 2)
    # trend_7d still comes from the day-7 player_status_daily row for "a".
    assert round(float(a["trend_7d_pct"]), 1) == round(100 * (12 - 10) / 10, 1)
    assert a["owner"] == "Rival" and float(a["predicted_ep"]) == 61.5 and float(a["p_start"]) == 0.7
    assert b["owner"] == "Kickbase" and b["team"] is None and b["points_prev"] is None
    assert b["predicted_ep"] is None and b["trend_24h_pct"] is None
    # "c"'s newest (and only) status row has no mv_change: trend_24h_pct is
    # null, not a fallback to mv_series. trend_7d_pct still falls back to
    # mv_series since "c" has no day-7 player_status_daily row.
    c = rows["c"]
    assert c["trend_24h_pct"] is None
    assert round(float(c["trend_7d_pct"]), 1) == round(100 * (12 - 10) / 10, 1)


def _fair_price_seed(dsn):
    """Two defenders with three appearances each, plus one with a single big
    game. Two points define the position's line exactly, so each of the two
    gets his own market value back as a fair price."""
    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [
            {"player_id": "d1", "last_name": "Dee One", "position": "Defender", "team_id": "7"},
            {"player_id": "d2", "last_name": "Dee Two", "position": "Defender", "team_id": "7"},
            {"player_id": "d3", "last_name": "Dee Three", "position": "Defender", "team_id": "7"},
        ]
    )
    # 60 points a game and 20 points a game, three games each.
    corpus.record_match_history(
        "d1", "7", _perf([("2026/2027", [_m(1, 5, 60), _m(2, 5, 60), _m(3, 5, 60)])])
    )
    corpus.record_match_history(
        "d2", "7", _perf([("2026/2027", [_m(1, 5, 20), _m(2, 5, 20), _m(3, 5, 20)])])
    )
    # One appearance, a huge score: the line would price him absurdly.
    corpus.record_match_history("d3", "7", _perf([("2026/2027", [_m(1, 5, 200)])]))
    for pid, mv in (("d1", 20_000_000), ("d2", 4_000_000), ("d3", 1_000_000)):
        corpus.record_status_daily(
            pid, date.today(), {"st": 0, "prob": 1, "mv": mv, "tid": "7", "tfhmvt": 0}, NOW
        )
    return LeagueStore(dsn=dsn)


def test_fair_price_is_what_his_average_is_worth_at_his_positions_rate(store_dsn):
    league = _fair_price_seed(store_dsn)
    rows = {r["player_id"]: r for r in league.player_table()}
    d1, d2 = rows["d1"], rows["d2"]
    assert d1["appearances"] == 3 and float(d1["avg_points"]) == 60.0
    assert abs(d1["fair_price"] - d1["market_value"]) <= 1
    assert round(float(d1["fair_value_gap"]), 1) == 0.0
    assert abs(d2["fair_price"] - d2["market_value"]) <= 1


def test_fair_price_needs_three_appearances(store_dsn):
    """One huge game must not price a player: the gap in points still shows,
    but the euro price says nothing until there is a season behind it."""
    league = _fair_price_seed(store_dsn)
    d3 = {r["player_id"]: r for r in league.player_table()}["d3"]
    assert d3["appearances"] == 1 and float(d3["avg_points"]) == 200.0
    assert d3["fair_price"] is None
    assert d3["fair_value_gap"] is not None


def test_fair_price_is_absent_without_any_average(store_dsn):
    league = _seed(store_dsn)
    c = {r["player_id"]: r for r in league.player_table()}["c"]
    assert c["avg_points"] is None and c["fair_price"] is None


def test_filters_and_order(store_dsn):
    league = _seed(store_dsn)
    assert [r["player_id"] for r in league.player_table(owner="Rival")] == ["a"]
    assert [
        r["player_id"] for r in league.player_table(position="Midfielder", order_by="points")
    ] == ["a", "b"]
