"""Market snapshots, squads, fixtures, table and clubs in the store (G1 Task 1)."""

from __future__ import annotations

from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore

T0 = 1_789_600_000.0


def _listing(pid, snapshot_at=T0, **over):
    row = {
        "snapshot_at": snapshot_at,
        "player_id": pid,
        "ask": 5_000_000,
        "market_value": 4_900_000,
        "mv_trend": 1,
        "seller_id": None,
        "offer_count": 0,
        "our_bid": None,
        "listed_at": snapshot_at - 3600,
        "expires_at": snapshot_at + 80_000,
        "status": 0,
        "lineup_probability": 1,
        "source": "session",
    }
    row.update(over)
    return row


def _squad_row(manager_id, pid, snapshot_at=T0, **over):
    row = {
        "snapshot_at": snapshot_at,
        "manager_id": manager_id,
        "player_id": pid,
        "market_value": 1_000_000,
        "gain_loss": 50_000,
        "on_market": False,
        "source": "session",
    }
    row.update(over)
    return row


def test_listings_are_append_only_snapshots(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    assert store.write_listings([_listing("a"), _listing("b")]) == 2
    assert store.write_listings([_listing("a", snapshot_at=T0 + 60, ask=5_100_000)]) == 1
    assert store.write_listings([_listing("a")]) == 1  # same key: upsert, no error
    latest = store.latest_market()
    assert [r["player_id"] for r in latest] == ["a"] and latest[0]["ask"] == 5_100_000
    assert store.latest_snapshot("market_listings") == T0 + 60


def test_owner_precedence_manager_then_market_then_kickbase(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    store.upsert_managers(
        [
            {
                "manager_id": "m1",
                "league_id": "L",
                "name": "Marco",
                "is_self": True,
                "updated_at": T0,
            },
            {
                "manager_id": "m2",
                "league_id": "L",
                "name": "Rival",
                "is_self": False,
                "updated_at": T0,
            },
        ]
    )
    store.write_squads([_squad_row("m1", "a"), _squad_row("m2", "b")])
    store.write_squads([_squad_row("m2", "c", snapshot_at=T0 + 60)])  # m2's own newest wins
    store.write_listings([_listing("d")])
    assert store.owner_of(["a", "b", "c", "d", "e"]) == {"a": "Marco", "c": "Rival", "d": "market"}
    # Newest is resolved per manager: m1's only snapshot (T0, "a") still stands even
    # though m2 has since written a newer one; "b" was m2's *older* snapshot, superseded
    # by their own "c" -- so "b" is absent now, not blanked league-wide. `e` is nowhere:
    # both absent => "Kickbase" for callers.


def test_upsert_managers_updates_the_name(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    store.upsert_managers(
        [
            {
                "manager_id": "m1",
                "league_id": "L",
                "name": "Old",
                "is_self": False,
                "updated_at": T0,
            }
        ]
    )
    store.upsert_managers(
        [
            {
                "manager_id": "m1",
                "league_id": "L",
                "name": "New",
                "is_self": False,
                "updated_at": T0 + 1,
            }
        ]
    )
    with store.connection() as conn:
        row = conn.execute("SELECT name FROM rehoboam.managers WHERE manager_id = 'm1'").fetchone()
    assert row["name"] == "New"


def test_fixtures_upsert_by_match_id(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    fx = {
        "match_id": "1",
        "season": "2026/2027",
        "day_number": 4,
        "kickoff": T0,
        "home_team_id": "2",
        "away_team_id": "9",
        "home_goals": None,
        "away_goals": None,
        "status": 0,
        "updated_at": T0,
    }
    assert store.upsert_fixtures([fx]) == 1
    assert (
        store.upsert_fixtures(
            [dict(fx, home_goals=2, away_goals=1, status=2, updated_at=T0 + 9000)]
        )
        == 1
    )
    with store.connection() as conn:
        row = conn.execute(
            "SELECT status, home_goals FROM rehoboam.fixtures WHERE match_id='1'"
        ).fetchone()
    assert (row["status"], row["home_goals"]) == (2, 2)


def test_table_and_teams(store_dsn):
    store = LeagueStore(dsn=store_dsn)
    CorpusStore(dsn=store_dsn).upsert_players(
        [
            {
                "player_id": "p",
                "first_name": None,
                "last_name": "P",
                "position": "Defender",
                "team_id": "7",
                "market_value": 1,
                "average_points": 1.0,
            },
            {
                "player_id": "q",
                "first_name": None,
                "last_name": "Q",
                "position": "Defender",
                "team_id": "8",
                "market_value": 1,
                "average_points": 1.0,
            },
        ]
    )
    assert sorted(store.teams_older_than(T0)) == ["7", "8"]
    store.upsert_teams(
        [{"team_id": "7", "name": "Club Seven", "short_name": "SEV", "updated_at": T0}]
    )
    assert store.teams_older_than(T0 - 1) == ["8"]
    assert store.teams_older_than(T0 + 1) == ["7", "8"]
    assert (
        store.write_table(
            [
                {
                    "season": "2026/2027",
                    "day_number": 3,
                    "team_id": "7",
                    "place": 1,
                    "previous_place": 2,
                    "points": 9,
                    "played": 3,
                    "goal_difference": 5,
                    "updated_at": T0,
                }
            ]
        )
        == 1
    )
    with store.connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM rehoboam.league_table").fetchone()["n"]
    assert n == 1
