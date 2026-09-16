"""League payloads → rows, pure (G1 Task 2). Field names probed live 2026-09-15."""

from __future__ import annotations

from types import SimpleNamespace

from rehoboam.enrichment.rows import (
    fixture_rows,
    league_table_rows,
    manager_rows,
    manager_squad_rows,
    market_listing_rows,
    own_squad_rows,
    team_row,
)

T0 = 1_789_600_000.0
MARKET = {
    "it": [
        {
            "i": "11",
            "tid": "7",
            "pos": 2,
            "st": 0,
            "prob": 1,
            "mv": 4_254_977,
            "mvt": 2,
            "prc": 4_300_000,
            "ofc": 1,
            "exs": 22_965,
            "dt": "2026-09-15T16:05:06Z",
            "u": {"i": "999", "n": "Rival"},
            "uoid": "3616202",
            "uop": 4_400_000,
        },
        {
            "i": "12",
            "tid": "8",
            "pos": 4,
            "st": 1,
            "mv": 6_832_673,
            "prc": 6_832_673,
            "ofc": 0,
            "dt": "2026-09-15T11:41:16Z",
        },
        {"tid": "8"},  # no id: dropped
    ]
}


def test_market_listing_rows():
    rows = market_listing_rows(MARKET, snapshot_at=T0, our_user_id="3616202", source="session")
    assert [r["player_id"] for r in rows] == ["11", "12"]
    a, b = rows
    assert a["ask"] == 4_300_000 and a["seller_id"] == "999" and a["offer_count"] == 1
    assert a["our_bid"] == 4_400_000 and a["expires_at"] == T0 + 22_965
    assert a["listed_at"] == 1_789_488_306.0  # 2026-09-15T16:05:06Z
    assert a["mv_trend"] == 2 and a["lineup_probability"] == 1 and a["source"] == "session"
    assert b["seller_id"] is None and b["expires_at"] is None and b["our_bid"] is None
    assert b["mv_trend"] is None and b["lineup_probability"] is None
    assert set(a) == {
        "snapshot_at",
        "player_id",
        "ask",
        "market_value",
        "mv_trend",
        "seller_id",
        "offer_count",
        "our_bid",
        "listed_at",
        "expires_at",
        "status",
        "lineup_probability",
        "source",
    }


def test_our_bid_only_when_the_offer_holder_is_us():
    payload = {"it": [dict(MARKET["it"][0], uoid="777", uop=1)]}
    rows = market_listing_rows(payload, snapshot_at=T0, our_user_id="3616202", source="ingest")
    assert rows[0]["our_bid"] is None


def test_ours_is_false_without_a_user_id_even_if_uop_is_present():
    """An empty `our_user_id` (what the ingest passes for `None`) must not mark
    every listing without a `uoid` as ours."""
    payload = {"it": [dict(MARKET["it"][1], uop=999)]}
    rows = market_listing_rows(payload, snapshot_at=T0, our_user_id="", source="session")
    assert rows[0]["our_bid"] is None


def test_ask_falls_back_to_mv_without_prc():
    payload = {"it": [dict(MARKET["it"][1], prc=None)]}
    rows = market_listing_rows(payload, snapshot_at=T0, our_user_id="3616202", source="session")
    assert rows[0]["ask"] == 6_832_673  # mv


def test_a_listing_with_neither_prc_nor_mv_is_dropped():
    payload = {"it": [dict(MARKET["it"][1], prc=None, mv=None)]}
    rows = market_listing_rows(payload, snapshot_at=T0, our_user_id="3616202", source="session")
    assert rows == []


def test_manager_squad_rows():
    items = [
        {"pi": "11", "mv": 5_000_000, "mvgl": 250_000, "iotm": True, "pn": "X"},
        {"pi": "12", "mv": 1_000_000},
        {"mv": 3},  # no id: dropped
    ]
    rows = manager_squad_rows("m2", items, snapshot_at=T0, source="ingest")
    assert [(r["player_id"], r["gain_loss"], r["on_market"]) for r in rows] == [
        ("11", 250_000, True),
        ("12", None, None),
    ]
    assert rows[0]["manager_id"] == "m2" and rows[0]["source"] == "ingest"


def test_own_squad_rows_from_player_objects():
    players = [
        SimpleNamespace(id="11", market_value=5_000_000),
        SimpleNamespace(id="", market_value=1),
    ]
    rows = own_squad_rows("m1", players, snapshot_at=T0, source="session")
    assert rows == [
        {
            "snapshot_at": T0,
            "manager_id": "m1",
            "player_id": "11",
            "market_value": 5_000_000,
            "gain_loss": None,
            "on_market": None,
            "source": "session",
        }
    ]


def test_manager_rows():
    ranking = {
        "us": [
            {"i": "3616202", "n": "Marco", "tv": 1},
            {"i": "999", "n": "Rival"},
            {"n": "x"},
        ]
    }
    rows = manager_rows(ranking, league_id="L", our_user_id="3616202", updated_at=T0)
    assert [(r["manager_id"], r["name"], r["is_self"]) for r in rows] == [
        ("3616202", "Marco", True),
        ("999", "Rival", False),
    ]
    assert rows[0]["league_id"] == "L" and rows[0]["updated_at"] == T0
    assert manager_rows({"it": ranking["us"]}, league_id="L", our_user_id="x", updated_at=T0)


def test_fixture_rows():
    schedule = {
        "it": [
            {
                "day": 3,
                "it": [
                    {
                        "mi": "1",
                        "dt": "2026-09-12T13:30:00Z",
                        "st": 2,
                        "t1": "2",
                        "t2": "9",
                        "t1g": 3,
                        "t2g": 1,
                    }
                ],
            },
            {
                "day": 4,
                "it": [
                    {
                        "mi": "2",
                        "dt": "2026-09-19T13:30:00Z",
                        "st": 0,
                        "t1": "5",
                        "t2": "6",
                    },
                    {"dt": "bad"},
                ],
            },
        ]
    }
    rows = fixture_rows(schedule, season="2026/2027", updated_at=T0)
    assert [(r["match_id"], r["day_number"], r["status"], r["home_goals"]) for r in rows] == [
        ("1", 3, 2, 3),
        ("2", 4, 0, None),
    ]
    assert rows[0]["kickoff"] == 1_789_219_800.0 and rows[0]["season"] == "2026/2027"


def test_league_table_rows():
    table = {
        "it": [
            {"tid": "7", "tn": "Seven", "cpl": 1, "pcpl": 2, "cp": 9, "mc": 3, "gd": 5},
            {"cpl": 2},
        ]
    }
    rows = league_table_rows(table, season="2026/2027", day_number=3, updated_at=T0)
    assert rows == [
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


def test_team_row():
    assert team_row({"tid": "7", "tn": "Club Seven", "ts": "SEV"}, updated_at=T0) == {
        "team_id": "7",
        "name": "Club Seven",
        "short_name": "SEV",
        "updated_at": T0,
    }
    assert team_row({"tn": "no id"}, updated_at=T0) is None
