"""The dashboard's six views (migration 007)."""

from __future__ import annotations

import time
from datetime import date

from rehoboam.services.calibration import CalibrationReport
from rehoboam.services.session_facts import IntegrityFailure, SessionFacts
from rehoboam.store import connect
from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore
from rehoboam.store.session_store import SessionStore

NOW = time.time()
SEASON = "2026/2027"


def _players(dsn):
    CorpusStore(dsn=dsn).upsert_players(
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
                "position": "Defender",
                "team_id": "8",
                "market_value": 2_000_000,
                "average_points": 10.0,
            },
        ]
    )
    LeagueStore(dsn=dsn).upsert_teams(
        [
            {
                "team_id": "7",
                "name": "Bayern",
                "short_name": "FCB",
                "updated_at": NOW,
                "crest_source": "content/file/bayern.svg",
            },
            {
                "team_id": "8",
                "name": "Schalke",
                "short_name": "S04",
                "updated_at": NOW,
                "crest_source": None,
            },
        ]
    )


def _managers(dsn):
    LeagueStore(dsn=dsn).upsert_managers(
        [
            {
                "manager_id": "me",
                "league_id": "1",
                "name": "Brownie",
                "is_self": True,
                "updated_at": NOW,
            },
            {
                "manager_id": "rival",
                "league_id": "1",
                "name": "Rival",
                "is_self": False,
                "updated_at": NOW,
            },
        ]
    )


def _rows(dsn, sql):
    with connect(dsn) as conn:
        return conn.execute(sql).fetchall()


def test_web_players_carries_the_columns_the_site_reads(store_dsn):
    _players(store_dsn)
    rows = {r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_players")}
    assert set(rows) == {"a", "b"}
    for key in (
        "name",
        "team",
        "team_id",
        "position",
        "market_value",
        "owner",
        "predicted_ep",
        "p_start",
        "fair_value_gap",
        "listed",
    ):
        assert key in rows["a"], key
    assert rows["a"]["team"] == "Bayern" and rows["a"]["team_id"] == "7"
    assert rows["a"]["listed"] is False


def test_web_players_flags_a_listed_player_its_owner_still_owns(store_dsn):
    _players(store_dsn)
    _managers(store_dsn)
    league = LeagueStore(dsn=store_dsn)
    league.write_squads(
        [
            {
                "snapshot_at": NOW,
                "manager_id": "rival",
                "player_id": "a",
                "market_value": 10_000_000,
                "gain_loss": 0,
                "on_market": True,
                "source": "test",
            }
        ]
    )
    league.write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 11_000_000,
                "market_value": 10_000_000,
                "mv_trend": 1,
                "seller_id": "rival",
                "offer_count": 0,
                "our_bid": None,
                "listed_at": NOW,
                "expires_at": NOW + 3600,
                "status": 0,
                "lineup_probability": 1,
                "source": "test",
            }
        ]
    )
    row = {r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_players")}["a"]
    # `player_table.owner` says "Rival"; only `listed` tells the page it is for sale.
    assert row["owner"] == "Rival" and row["listed"] is True


def test_web_players_column_order_is_unchanged_plus_the_appended_columns(store_dsn):
    """Migration 021 regression: every column `web_players` had before is
    still there, with its old name, in its old order -- only the three new
    ones (021) are appended at the end."""
    _players(store_dsn)
    with connect(store_dsn) as conn:
        cols = [
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='rehoboam' AND table_name='web_players' "
                "ORDER BY ordinal_position"
            ).fetchall()
        ]
    assert cols == [
        "player_id",
        "name",
        "team",
        "team_id",
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
        "listed",
        "next_mv_change",
        "next_mv_pct",
        "fair_price",
        "image_path",
        "crest_path",
        "availability",
        # where he stands at his position (026)
        "avg_points_rank_pos",
        "ep_rank_pos",
    ]


def test_web_market_column_order_is_unchanged_plus_trend_24h_eur_and_images(store_dsn):
    with connect(store_dsn) as conn:
        cols = [
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='rehoboam' AND table_name='web_market' "
                "ORDER BY ordinal_position"
            ).fetchall()
        ]
    assert cols == [
        "snapshot_at",
        "player_id",
        "name",
        "team",
        "position",
        "ask",
        "market_value",
        "mv_trend",
        "offer_count",
        "our_bid",
        "listed_at",
        "expires_at",
        "status",
        "lineup_probability",
        "seller",
        "is_ours",
        "predicted_ep",
        "p_start",
        "fair_value_gap",
        "points",
        "avg_points",
        "next_mv_change",
        "next_mv_pct",
        "fair_price",
        "trend_24h_pct",
        "points_per_million",
        "trend_24h_eur",
        "image_path",
        "crest_path",
        "availability",
    ]


def test_web_players_carries_image_path_crest_path_and_availability(store_dsn):
    """`image_path`/`crest_path` stay null until a later task's sync writes
    them (021's own writers only fill `*_source`), so this sets them via a
    raw update to prove the join/select plumbing, same as player_table's own
    test. `availability` is the newest `player_status_daily.status`."""
    _players(store_dsn)
    CorpusStore(dsn=store_dsn).record_status_daily("a", date.today(), {"st": 4}, NOW)
    with connect(store_dsn) as conn:
        conn.execute(
            "update rehoboam.player_universe set image_path = %s where player_id = 'a'",
            ("players/a.png",),
        )
        conn.execute(
            "update rehoboam.teams set crest_path = %s where team_id = '7'",
            ("teams/7.png",),
        )
    row = {r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_players")}["a"]
    assert row["image_path"] == "players/a.png"
    assert row["crest_path"] == "teams/7.png"
    assert row["availability"] == 4


def test_web_players_availability_is_null_without_a_status_row(store_dsn):
    _players(store_dsn)
    row = {r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_players")}["b"]
    assert row["availability"] is None


def test_web_market_carries_trend_24h_eur_from_the_newest_status_row(store_dsn):
    _players(store_dsn)
    CorpusStore(dsn=store_dsn).record_status_daily(
        "a", date.today(), {"mv": 10_000_000, "tfhmvt": -250_000}, NOW
    )
    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 10_000_000,
                "market_value": 10_000_000,
                "mv_trend": 0,
                "seller_id": None,
                "offer_count": 0,
                "our_bid": None,
                "listed_at": NOW,
                "expires_at": NOW + 10,
                "status": 0,
                "lineup_probability": 1,
                "source": "test",
            }
        ]
    )
    row = _rows(store_dsn, "select * from rehoboam.web_market")[0]
    assert row["trend_24h_eur"] == -250_000
    assert "trend_24h_pct" in row, "the existing percent column must not be removed"


def test_web_market_carries_image_path_and_crest_path(store_dsn):
    """Round 1 fix (task 10b): `web_market` already left-joins `web_players`
    for `name`/`team`/etc, so `image_path`/`crest_path` ride along on that
    same join rather than the web app re-joining the whole view a second
    time per page load. Same raw-update technique as
    `test_web_players_carries_image_path_crest_path_and_availability`, since
    021's own writers only fill `*_source`."""
    _players(store_dsn)
    with connect(store_dsn) as conn:
        conn.execute(
            "update rehoboam.player_universe set image_path = %s where player_id = 'a'",
            ("players/a.png",),
        )
        conn.execute(
            "update rehoboam.teams set crest_path = %s where team_id = '7'",
            ("teams/7.png",),
        )
    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 10_000_000,
                "market_value": 10_000_000,
                "mv_trend": 0,
                "seller_id": None,
                "offer_count": 0,
                "our_bid": None,
                "listed_at": NOW,
                "expires_at": NOW + 10,
                "status": 0,
                "lineup_probability": 1,
                "source": "test",
            }
        ]
    )
    row = _rows(store_dsn, "select * from rehoboam.web_market")[0]
    assert row["image_path"] == "players/a.png"
    assert row["crest_path"] == "teams/7.png"


def test_web_market_carries_availability_from_the_web_players_join(store_dsn):
    """Round 2 fix (finding 3): `web_market` left-joins `web_players` for
    `name`/`team`/`image_path`/etc already -- `availability` rides along on
    that same join, so Market's fitness dot reads the newest
    `player_status_daily.status`, same as `PlayerList`'s dot on Players."""
    _players(store_dsn)
    CorpusStore(dsn=store_dsn).record_status_daily("a", date.today(), {"st": 4}, NOW)
    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 10_000_000,
                "market_value": 10_000_000,
                "mv_trend": 0,
                "seller_id": None,
                "offer_count": 0,
                "our_bid": None,
                "listed_at": NOW,
                "expires_at": NOW + 10,
                "status": 0,
                "lineup_probability": 1,
                "source": "test",
            }
        ]
    )
    row = _rows(store_dsn, "select * from rehoboam.web_market")[0]
    assert row["availability"] == 4


def test_web_ownership_takes_each_managers_own_newest_snapshot(store_dsn):
    _players(store_dsn)
    _managers(store_dsn)
    league = LeagueStore(dsn=store_dsn)
    league.write_squads(
        [
            {
                "snapshot_at": NOW,
                "manager_id": "me",
                "player_id": "a",
                "market_value": 10_000_000,
                "gain_loss": 0,
                "on_market": False,
                "source": "t",
            },
            {
                "snapshot_at": NOW,
                "manager_id": "rival",
                "player_id": "b",
                "market_value": 2_000_000,
                "gain_loss": 0,
                "on_market": False,
                "source": "t",
            },
        ]
    )
    # A later snapshot that contains only our own squad must not blank the rival.
    league.write_squads(
        [
            {
                "snapshot_at": NOW + 60,
                "manager_id": "me",
                "player_id": "a",
                "market_value": 10_500_000,
                "gain_loss": 0,
                "on_market": False,
                "source": "t",
            },
        ]
    )
    rows = {r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_ownership")}
    assert rows["a"]["manager"] == "Brownie" and rows["a"]["is_self"] is True
    assert rows["a"]["market_value"] == 10_500_000
    assert rows["b"]["manager"] == "Rival", "the rival's older snapshot still owns their player"


def test_web_market_reads_only_the_newest_snapshot_and_names_the_seller(store_dsn):
    _players(store_dsn)
    _managers(store_dsn)
    league = LeagueStore(dsn=store_dsn)
    old = {
        "snapshot_at": NOW,
        "player_id": "a",
        "ask": 1,
        "market_value": 10_000_000,
        "mv_trend": 0,
        "seller_id": None,
        "offer_count": 0,
        "our_bid": None,
        "listed_at": NOW,
        "expires_at": NOW + 10,
        "status": 0,
        "lineup_probability": 1,
        "source": "t",
    }
    league.write_listings([old])
    league.write_listings(
        [
            {**old, "snapshot_at": NOW + 60, "ask": 11_000_000, "seller_id": "me"},
            {
                **old,
                "snapshot_at": NOW + 60,
                "player_id": "b",
                "ask": 2_500_000,
                "seller_id": "rival",
                "market_value": 2_000_000,
            },
        ]
    )
    rows = {r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_market")}
    assert set(rows) == {"a", "b"}, "the older snapshot is gone"
    assert rows["a"]["ask"] == 11_000_000
    assert rows["a"]["seller"] == "Brownie" and rows["a"]["is_ours"] is True
    assert rows["b"]["seller"] == "Rival" and rows["b"]["is_ours"] is False


def test_web_market_calls_an_unowned_listing_kickbase(store_dsn):
    _players(store_dsn)
    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 9,
                "market_value": 10_000_000,
                "mv_trend": 0,
                "seller_id": None,
                "offer_count": 0,
                "our_bid": None,
                "listed_at": NOW,
                "expires_at": NOW + 10,
                "status": 0,
                "lineup_probability": 1,
                "source": "t",
            },
        ]
    )
    row = _rows(store_dsn, "select * from rehoboam.web_market")[0]
    assert row["seller"] == "Kickbase" and row["is_ours"] is False


def _facts(session_id, *, app="function", dry_run=False, started_at=NOW, **kw):
    return SessionFacts(
        session_id=session_id,
        app=app,
        mode=kw.pop("mode", "lineup_only"),
        dry_run=dry_run,
        started_at=started_at,
        **kw,
    )


def _prediction(session_id, player_id, *, owned=True, in_best_11=True, ep=100.0):
    return {
        "session_id": session_id,
        "player_id": player_id,
        "season": SEASON,
        "day_number": 4,
        "kickoff": NOW + 86400,
        "predicted_at": NOW,
        "predicted_ep": ep,
        "p_status": {"1": 0.1, "5": 0.8},
        "rate": 1.0,
        "prev_status": 1,
        "live_status": 1,
        "position": "Midfielder",
        "team_id": "7",
        "owned": owned,
        "listed": False,
        "in_best_11": in_best_11,
        "live_ep": ep,
        "data_grade": "A",
        "app": "function",
        "dry_run": False,
        "backfill": False,
    }


def test_web_squad_uses_the_newest_real_session_not_a_dry_run(store_dsn):
    _players(store_dsn)
    sessions = SessionStore(dsn=store_dsn)
    calib = CalibrationStore(dsn=store_dsn)
    sessions.record(_facts("real", started_at=NOW, legal_formation="4-3-3", budget=1_725_739))
    sessions.record(_facts("dry", app="cli", dry_run=True, started_at=NOW + 600))
    calib.write_predictions([_prediction("real", "a"), _prediction("dry", "b")])
    rows = _rows(store_dsn, "select * from rehoboam.web_squad")
    assert [r["player_id"] for r in rows] == ["a"]
    assert rows[0]["session_id"] == "real"
    assert rows[0]["legal_formation"] == "4-3-3" and rows[0]["budget"] == 1_725_739
    assert rows[0]["in_best_11"] is True and rows[0]["p_start"] == 0.8
    assert rows[0]["cost_basis"] is None and rows[0]["gain_loss"] is None


def test_web_squad_ignores_a_live_cli_session(store_dsn):
    # `rehoboam auto` writes app='cli' even when it trades for real; only the
    # Azure Function's session is "the" session the squad page shows.
    _players(store_dsn)
    sessions = SessionStore(dsn=store_dsn)
    calib = CalibrationStore(dsn=store_dsn)
    sessions.record(_facts("real", started_at=NOW, legal_formation="4-3-3"))
    sessions.record(
        _facts("cli_live", app="cli", dry_run=False, started_at=NOW + 600, legal_formation="3-4-3")
    )
    calib.write_predictions([_prediction("real", "a"), _prediction("cli_live", "b")])
    rows = _rows(store_dsn, "select * from rehoboam.web_squad")
    assert [r["player_id"] for r in rows] == ["a"]
    assert rows[0]["session_id"] == "real" and rows[0]["legal_formation"] == "4-3-3"


def test_web_squad_carries_cost_basis_and_gain_when_the_purchase_is_known(store_dsn):
    _players(store_dsn)
    SessionStore(dsn=store_dsn).record(_facts("real", legal_formation="4-3-3"))
    CalibrationStore(dsn=store_dsn).write_predictions([_prediction("real", "a")])
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.tracked_purchases "
            "(player_id, player_name, buy_price, buy_date, source) "
            "values ('a', 'Alpha', 8000000, %s, 'test')",
            (NOW,),
        )
    row = _rows(store_dsn, "select * from rehoboam.web_squad")[0]
    assert row["cost_basis"] == 8_000_000
    assert row["gain_loss"] == 2_000_000


def test_web_squad_survives_a_session_whose_roster_never_wrote(store_dsn):
    # `session_facts` (step 8) can exist with no matching `predictions` rows
    # (step 2a's best-effort write failed, or legitimately wrote nothing
    # owned) -- an inner join here would make the whole view return zero
    # rows, indistinguishable from "no session has ever run".
    SessionStore(dsn=store_dsn).record(_facts("real", legal_formation="4-3-3", budget=1_725_739))
    rows = _rows(store_dsn, "select * from rehoboam.web_squad")
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "real"
    assert row["legal_formation"] == "4-3-3" and row["budget"] == 1_725_739
    assert row["player_id"] is None


def test_web_session_summary_flattens_an_ingest_run_and_its_rules(store_dsn):
    sessions = SessionStore(dsn=store_dsn)
    sessions.record(
        _facts(
            "ingest1",
            app="external",
            mode="ingest",
            extra={
                "requests": 1185,
                "failed": 0,
                "status_written": 371,
                "performance_fetched": 371,
                "transfers_fetched": 371,
                "universe_size": 462,
                "stopped_by": "deadline",
                "league": {
                    "failed": 0,
                    "teams": 24,
                    "fixtures": 306,
                    "listings": 48,
                    "squads": 159,
                },
                "calibration": {"settled": [3], "reported": [], "waiting": {}},
            },
        )
    )
    sessions.record(_facts("sess1", extra={"league_state": {"squads": 158}}))
    sessions.record_failures(
        "sess1", [IntegrityFailure("I4", "2 owned player(s) without a cost basis")]
    )
    rows = {
        r["session_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_session_summary")
    }
    ingest = rows["ingest1"]
    assert ingest["requests"] == 1185 and ingest["status_written"] == 371
    assert ingest["universe_size"] == 462 and ingest["stopped_by"] == "deadline"
    assert ingest["league_teams"] == 24 and ingest["league_failed"] == 0
    assert ingest["calibration_settled"] == [3]
    assert ingest["integrity_rules"] == []
    session = rows["sess1"]
    assert session["integrity_rules"] == ["I4"]
    assert session["integrity_details"] == {"I4": "2 owned player(s) without a cost basis"}
    assert session["league_state_squads"] == 158
    assert session["requests"] is None, "a trading session has no ingest counters"
    assert ingest["integrity_details"] == {}


def test_web_calibration_keeps_an_empty_settled_report(store_dsn):
    # `write_calibration` takes the report object, not loose metrics: it calls
    # `report.as_row()` and writes rows + report in one transaction.
    empty = CalibrationReport(
        n=0,
        n_unpredicted=0,
        n_stale_rows=0,
        mae=None,
        bias=None,
        spearman=None,
        baseline_spearman=None,
        spearman_played=None,
        top11_regret=None,
        baseline_top11_regret=None,
        squad_regret=None,
        live_spearman=None,
        live_n=0,
    )
    CalibrationStore(dsn=store_dsn).write_calibration(
        season=SEASON,
        day_number=1,
        backfill=False,
        rows=[],
        report=empty,
        gate=None,
        computed_at=NOW,
    )
    rows = _rows(store_dsn, "select * from rehoboam.web_calibration")
    assert len(rows) == 1
    assert rows[0]["n"] == 0 and rows[0]["gate"] is None
    assert rows[0]["backfill"] is False


def _berlin_live_day(dsn):
    """The day web_mv_forecast shows, computed with the database's own clock."""
    return _rows(
        dsn,
        "select ((now() at time zone 'Europe/Berlin') + interval '2 hours')::date as d",
    )[0]["d"]


def _forecast(dsn, player_id, target_day, *, change, pct, scored=False):
    with connect(dsn) as conn:
        conn.execute(
            "insert into rehoboam.mv_forecasts (player_id, target_day, made_at, method, "
            "base_mv, last_change, predicted_change, predicted_pct, scored_at, outcome, "
            "actual_change, actual_pct) values (%s, %s, %s, 'momentum-v1', 10000000, "
            "100000, %s, %s, %s, %s, %s, %s)",
            (
                player_id,
                target_day,
                NOW,
                change,
                pct,
                NOW if scored else None,
                "scored" if scored else None,
                change if scored else None,
                pct if scored else None,
            ),
        )


def test_web_mv_forecast_shows_only_the_live_days_unscored_forecast(store_dsn):
    from datetime import timedelta

    _players(store_dsn)
    live = _berlin_live_day(store_dsn)
    _forecast(store_dsn, "a", live, change=90_000, pct=0.009)
    _forecast(store_dsn, "a", live - timedelta(days=1), change=5, pct=0.5)
    _forecast(store_dsn, "b", live, change=-18_000, pct=-0.009, scored=True)
    rows = _rows(store_dsn, "select * from rehoboam.web_mv_forecast")
    assert [(r["player_id"], r["target_day"]) for r in rows] == [("a", live)]
    assert float(rows[0]["predicted_pct"]) == 0.9  # percent, two decimals
    players = {r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_players")}
    assert players["a"]["next_mv_change"] == 90_000
    assert float(players["a"]["next_mv_pct"]) == 0.9
    assert players["b"]["next_mv_change"] is None and players["b"]["next_mv_pct"] is None


def test_web_market_carries_the_listed_players_forecast(store_dsn):
    _players(store_dsn)
    _forecast(store_dsn, "a", _berlin_live_day(store_dsn), change=-45_000, pct=-0.0045)
    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 10_000_000,
                "market_value": 10_000_000,
                "mv_trend": 2,
                "seller_id": None,
                "offer_count": 0,
                "our_bid": None,
                "listed_at": None,
                "expires_at": NOW + 3600,
                "status": 0,
                "lineup_probability": 1,
                "source": "test",
            }
        ]
    )
    row = _rows(store_dsn, "select * from rehoboam.web_market")[0]
    assert row["next_mv_change"] == -45_000
    assert float(row["next_mv_pct"]) == -0.45


def test_web_mv_accuracy_counts_hits_misses_and_unscorable_rows(store_dsn):
    from datetime import date

    day = date(2026, 9, 16)
    with connect(store_dsn) as conn:
        for pid, pred, actual, outcome in (
            ("a", 100, 80, "scored"),  # right direction, miss 20 € / 0.2 pp
            ("b", 100, -50, "scored"),  # wrong direction, miss 150 € / 1.5 pp
            ("c", 0, 30, "scored"),  # flat forecast: not directional
            ("d", 100, None, "unscorable"),
        ):
            conn.execute(
                "insert into rehoboam.mv_forecasts (player_id, target_day, made_at, method, "
                "base_mv, last_change, predicted_change, predicted_pct, scored_at, outcome, "
                "actual_change, actual_pct) values (%s, %s, 1, 'momentum-v1', 10000, 100, "
                "%s, %s, 2, %s, %s, %s)",
                (
                    pid,
                    day,
                    pred,
                    pred / 10000,
                    outcome,
                    actual,
                    None if actual is None else actual / 10000,
                ),
            )
        # an unscored row never counts
        conn.execute(
            "insert into rehoboam.mv_forecasts (player_id, target_day, made_at, method, "
            "base_mv, last_change, predicted_change, predicted_pct) "
            "values ('e', %s, 1, 'momentum-v1', 10000, 100, 100, 0.01)",
            (day,),
        )
    (row,) = _rows(store_dsn, "select * from rehoboam.web_mv_accuracy")
    assert row["target_day"] == day
    assert (
        row["scored"],
        row["unscorable"],
        row["directional"],
        row["direction_hits"],
    ) == (
        3,
        1,
        2,
        1,
    )
    # misses: |0.8-1.0|=0.2, |-0.5-1.0|=1.5, |0.3-0|=0.3 pp -> mean 0.67
    assert float(row["mae_pct"]) == 0.67
    # no change: 0.8, 0.5, 0.3 pp -> mean 0.53
    assert float(row["baseline_mae_pct"]) == 0.53
    assert row["mae_eur"] == 67  # (20 + 150 + 30) / 3 = 66.67
    assert row["baseline_mae_eur"] == 53  # (80 + 50 + 30) / 3 = 53.33


def test_web_players_and_web_market_carry_the_fair_price(store_dsn):
    """Migration 009's `fair_price` (euros) rides along with `fair_value_gap`
    (points) on both views the site reads."""
    _players(store_dsn)
    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 10_000_000,
                "market_value": 10_000_000,
                "mv_trend": 1,
                "seller_id": None,
                "offer_count": 0,
                "our_bid": None,
                "listed_at": None,
                "expires_at": NOW + 3600,
                "status": 0,
                "lineup_probability": 1,
                "source": "test",
            }
        ]
    )
    players = {r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_players")}
    assert "fair_price" in players["a"]
    market = _rows(store_dsn, "select * from rehoboam.web_market")[0]
    assert "fair_price" in market
    # Migration 010: the Market page reads yesterday's move and points per
    # million from the same row, not from a second query.
    assert "trend_24h_pct" in market and "points_per_million" in market
