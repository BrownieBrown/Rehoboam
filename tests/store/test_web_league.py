"""Migration 023: the League tab's views -- managers, their matchdays, their
transfers -- read only what the store already holds."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from rehoboam.store import connect

NOW = time.time()
SEASON_START = NOW - 30 * 86400
LAST_SEASON = NOW - 200 * 86400


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(dsn):
    with connect(dsn) as conn:
        conn.execute(
            "insert into rehoboam.managers (manager_id, league_id, name, is_self, updated_at) "
            "values ('me', 'L', 'Brownie', true, %s), ('r1', 'L', 'Rival', false, %s)",
            (NOW, NOW),
        )
        conn.execute(
            "insert into rehoboam.fixtures (match_id, season, day_number, kickoff, "
            "home_team_id, away_team_id, status, updated_at) values "
            "('old', '2025/2026', 1, %s, '1', '2', 2, %s), "
            "('new', '2026/2027', 1, %s, '1', '2', 2, %s)",
            (LAST_SEASON, NOW, SEASON_START, NOW),
        )
        rank = (
            "insert into rehoboam.league_rank_history (snapshot_at, league_id, manager_id, "
            "day_number, rank_overall, rank_matchday, total_points, matchday_points, "
            "team_value, is_self) values (%s, 'L', %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        for row in (
            # Last season's backfill: same day numbers, far bigger totals.
            (LAST_SEASON + 86400, "me", 1, 1, 1, 9999, 9999, 1, 1),
            (LAST_SEASON + 86400, "r1", 1, 2, 2, 8888, 8888, 1, 0),
            # This season, matchday 1 -- read twice; the later reading wins.
            (SEASON_START + 86400, "me", 1, 2, 2, 400, 400, 100, 1),
            (SEASON_START + 2 * 86400, "me", 1, 2, 2, 410, 410, 100, 1),
            (SEASON_START + 86400, "r1", 1, 1, 1, 900, 900, 200, 0),
            # Matchday 2.
            (SEASON_START + 8 * 86400, "me", 2, 2, 1, 1410, 1000, 110, 1),
            (SEASON_START + 8 * 86400, "r1", 2, 1, 2, 1700, 800, 210, 0),
            # A manager who has left: in the history, not in `managers`.
            (SEASON_START + 8 * 86400, "gone", 2, 3, 3, 100, 50, 10, 0),
        ):
            conn.execute(rank, row)
        conn.execute(
            "insert into rehoboam.manager_profile_history (snapshot_at, league_id, manager_id, "
            "transfer_pnl, matchday_wins, is_self) values "
            "(%s, 'L', 'r1', -5, 0, 0), (%s, 'L', 'r1', 1500000, 1, 0)",
            (NOW - 86400, NOW),
        )
        squad = (
            "insert into rehoboam.manager_squads (snapshot_at, manager_id, player_id, "
            "market_value, gain_loss, on_market, source) values (%s, 'r1', %s, %s, 0, %s, 't')"
        )
        # An older snapshot with a player he has since sold.
        conn.execute(squad, (NOW - 86400, "sold", 9_000_000, False))
        for i in range(12):
            conn.execute(squad, (NOW, f"p{i}", 1_000_000, i == 0))
        transfer = (
            "insert into rehoboam.manager_transfers (league_id, manager_id, transfer_dt, "
            "player_id, player_name, transfer_type, transfer_price) "
            "values ('L', %s, %s, %s, %s, %s, %s)"
        )
        conn.execute(transfer, ("r1", _iso(NOW - 3600), "p1", "Pee One", 1, 5_000_000))
        conn.execute(transfer, ("r1", _iso(NOW - 2 * 86400), "sold", "Sold", 2, 9_500_000))
        conn.execute(transfer, ("r1", _iso(NOW - 30 * 86400), "p2", "Pee Two", 1, 700_000))
        conn.execute(transfer, ("gone", _iso(NOW - 3600), "p3", "Pee Three", 1, 1))


def _rows(dsn, sql):
    with connect(dsn) as conn:
        return conn.execute(sql).fetchall()


def test_web_managers_shows_this_seasons_newest_standing(store_dsn):
    _seed(store_dsn)
    rows = {r["manager_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_managers")}
    assert set(rows) == {"me", "r1"}  # the manager who left is not a row
    me, rival = rows["me"], rows["r1"]
    assert me["is_self"] is True and me["name"] == "Brownie"
    assert (me["day_number"], me["rank_overall"], me["total_points"]) == (2, 2, 1410)
    assert (me["rank_matchday"], me["matchday_points"]) == (1, 1000)
    # Behind the leader among CURRENT managers, this season -- not last
    # season's 9999 and not the departed manager.
    assert me["points_behind_leader"] == 290 and rival["points_behind_leader"] == 0


def test_web_managers_counts_the_newest_squad_and_a_week_of_dealing(store_dsn):
    _seed(store_dsn)
    rival = {r["manager_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_managers")}[
        "r1"
    ]
    assert rival["squad_size"] == 12 and rival["squad_value"] == 12_000_000
    assert rival["on_market"] == 1
    assert rival["transfer_pnl"] == 1_500_000 and rival["matchday_wins"] == 1
    # One buy an hour ago, one sale two days ago; the month-old buy is out.
    assert (rival["buys_7d"], rival["sells_7d"]) == (1, 1)
    me = {r["manager_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_managers")}["me"]
    # No squad snapshot, no profile, no transfers: a row all the same.
    assert me["squad_size"] is None and me["transfer_pnl"] is None
    assert (me["buys_7d"], me["sells_7d"]) == (0, 0)


def test_web_manager_matchdays_keeps_the_newest_reading_of_each_day(store_dsn):
    _seed(store_dsn)
    rows = _rows(
        store_dsn,
        "select * from rehoboam.web_manager_matchdays where manager_id = 'me' order by day_number",
    )
    assert [(r["day_number"], r["matchday_points"]) for r in rows] == [(1, 410), (2, 1000)]
    gone = _rows(
        store_dsn, "select * from rehoboam.web_manager_matchdays where manager_id = 'gone'"
    )
    assert gone == []


def test_web_manager_transfers_names_the_direction(store_dsn):
    _seed(store_dsn)
    rows = _rows(
        store_dsn,
        "select * from rehoboam.web_manager_transfers where manager_id = 'r1' "
        "order by transfer_at desc",
    )
    assert [(r["player_name"], r["kind"], r["price"]) for r in rows] == [
        ("Pee One", "buy", 5_000_000),
        ("Sold", "sell", 9_500_000),
        ("Pee Two", "buy", 700_000),
    ]
    assert rows[0]["transfer_at"] > datetime.now(tz=timezone.utc) - timedelta(hours=2)
    assert (
        _rows(store_dsn, "select 1 from rehoboam.web_manager_transfers where manager_id='gone'")
        == []
    )


def test_web_ownership_carries_what_the_squad_table_shows(store_dsn):
    _seed(store_dsn)
    rows = _rows(store_dsn, "select * from rehoboam.web_ownership where manager_id = 'r1'")
    assert len(rows) == 12  # the newest snapshot only
    for col in ("points", "avg_points", "availability", "image_path", "crest_path"):
        assert col in rows[0]
