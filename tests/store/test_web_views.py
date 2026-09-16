"""The dashboard's six views (migration 007)."""

from __future__ import annotations

import time

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
            {"team_id": "7", "name": "Bayern", "short_name": "FCB", "updated_at": NOW},
            {"team_id": "8", "name": "Schalke", "short_name": "S04", "updated_at": NOW},
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
    assert session["league_state_squads"] == 158
    assert session["requests"] is None, "a trading session has no ingest counters"


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
