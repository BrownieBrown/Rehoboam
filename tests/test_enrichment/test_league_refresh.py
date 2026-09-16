"""One league refresh per ingest run: market, managers, squads, page-0 transfers, fixtures, table, clubs."""

from __future__ import annotations

from unittest.mock import MagicMock

from rehoboam.bid_learner import BidLearner
from rehoboam.enrichment.league_refresh import run_league_refresh
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore

NOW = 1_789_600_000.0
MARKET = {
    "it": [
        {
            "i": "11",
            "tid": "7",
            "pos": 2,
            "prc": 1,
            "mv": 1,
            "exs": 10,
            "dt": "2026-09-15T16:05:06Z",
        }
    ]
}
RANKING = {"us": [{"i": "me", "n": "Marco"}, {"i": "m2", "n": "Rival"}], "day": 4}
SCHEDULE = {
    "it": [
        {
            "day": 4,
            "it": [{"mi": "1", "dt": "2026-09-19T13:30:00Z", "st": 0, "t1": "7", "t2": "8"}],
        }
    ]
}
TABLE = {
    "it": [
        {"tid": "7", "cpl": 1, "cp": 9, "mc": 3, "gd": 5},
        {"tid": "8", "cpl": 2, "cp": 6, "mc": 3, "gd": 0},
    ]
}


def _client(fail_squad_for=None):
    c = MagicMock()
    c.last_market_payload = None

    def get_market(league_id):
        c.last_market_payload = MARKET
        return []

    c.get_market.side_effect = get_market
    c.get_league_ranking.return_value = RANKING

    def squad(league_id, mid):
        if mid == fail_squad_for:
            raise RuntimeError("boom")
        return {"it": [{"pi": f"p-{mid}", "mv": 1}]}

    c.get_manager_squad.side_effect = squad
    c.get_manager_transfer_history.return_value = {
        "it": [
            {
                "pi": "11",
                "pn": "X",
                "tty": 1,
                "trp": 1_000,
                "dt": "2026-09-14T10:00:00Z",
            }
        ]
    }
    c.get_competition_matchdays.return_value = SCHEDULE
    c.get_competition_table.return_value = TABLE
    c.get_team_profile.side_effect = lambda league_id, tid: {
        "tid": tid,
        "tn": f"Club {tid}",
        "ts": tid,
    }
    return c


def _seed_universe(dsn):
    CorpusStore(dsn=dsn).upsert_players(
        [
            {
                "player_id": "11",
                "first_name": None,
                "last_name": "A",
                "position": "Defender",
                "team_id": "7",
                "market_value": 1,
                "average_points": 1.0,
            },
            {
                "player_id": "12",
                "first_name": None,
                "last_name": "B",
                "position": "Defender",
                "team_id": "8",
                "market_value": 1,
                "average_points": 1.0,
            },
        ]
    )


def test_a_refresh_writes_everything_and_counts_requests(store_dsn):
    _seed_universe(store_dsn)
    league, learner, client = (
        LeagueStore(dsn=store_dsn),
        BidLearner(dsn=store_dsn),
        _client(),
    )
    stats = run_league_refresh(
        client,
        league,
        learner,
        league_id="L",
        our_user_id="me",
        season="2026/2027",
        now=NOW,
    )
    assert (stats.listings, stats.managers, stats.squads, stats.transfers) == (
        1,
        2,
        2,
        2,
    )
    assert (stats.fixtures, stats.table_rows, stats.teams, stats.failed) == (1, 2, 2, 0)
    # market 1 + ranking 1 + squads 2 + transfers 2 + schedule 1 + table 1 + teams 2
    assert stats.requests == 10
    assert league.owner_of(["p-m2", "p-me", "11"]) == {
        "p-m2": "Rival",
        "p-me": "Marco",
        "11": "market",
    }
    with league.connection() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM rehoboam.manager_transfers").fetchone()["n"]
            == 2
        )
        assert (
            conn.execute("SELECT name FROM rehoboam.teams WHERE team_id='7'").fetchone()["name"]
            == "Club 7"
        )
        assert (
            conn.execute("SELECT day_number FROM rehoboam.league_table LIMIT 1").fetchone()[
                "day_number"
            ]
            == 4
        )


def test_a_failing_manager_call_is_counted_and_the_rest_proceeds(store_dsn):
    _seed_universe(store_dsn)
    league, learner = LeagueStore(dsn=store_dsn), BidLearner(dsn=store_dsn)
    stats = run_league_refresh(
        _client(fail_squad_for="m2"),
        league,
        learner,
        league_id="L",
        our_user_id="me",
        season="2026/2027",
        now=NOW,
    )
    assert stats.failed == 1 and stats.squads == 1 and stats.fixtures == 1


def test_teams_are_refreshed_weekly_only(store_dsn):
    _seed_universe(store_dsn)
    league, learner = LeagueStore(dsn=store_dsn), BidLearner(dsn=store_dsn)
    run_league_refresh(
        _client(),
        league,
        learner,
        league_id="L",
        our_user_id="me",
        season="2026/2027",
        now=NOW,
    )
    again = run_league_refresh(
        _client(),
        league,
        learner,
        league_id="L",
        our_user_id="me",
        season="2026/2027",
        now=NOW + 3600,
    )
    assert again.teams == 0 and again.requests == 8
