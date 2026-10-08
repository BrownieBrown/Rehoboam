"""Understat snapshots in the store."""

from __future__ import annotations

from datetime import date

from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.understat_store import UnderstatStore


def _row(uid, day, xg, player_id=None):
    return {
        "season": "2026/2027",
        "understat_id": uid,
        "day": day,
        "player_name": "P " + uid,
        "team_title": "Freiburg",
        "player_id": player_id,
        "position": "F",
        "games": 4,
        "time_played": 300,
        "goals": 2,
        "assists": 1,
        "shots": 9,
        "key_passes": 3,
        "npg": 2,
        "yellow_cards": 0,
        "red_cards": 0,
        "xg": xg,
        "xa": 0.4,
        "npxg": xg,
        "xg_chain": 2.0,
        "xg_buildup": 0.3,
        "fetched_at": 100.0,
    }


def test_write_keeps_one_row_per_player_and_day_and_latest_reads_the_newest(store_dsn):
    CorpusStore(dsn=store_dsn).upsert_players(
        [{"player_id": "x", "last_name": "X", "position": "Forward", "team_id": "5"}]
    )
    store = UnderstatStore(dsn=store_dsn)
    assert store.latest_fetched_at("2026/2027") is None
    assert (
        store.write([_row("1", date(2026, 10, 1), 1.0, "x"), _row("2", date(2026, 10, 1), 0.5)])
        == 2
    )
    assert store.write([dict(_row("1", date(2026, 10, 1), 1.2, "x"), fetched_at=200.0)]) == 1
    assert store.write([dict(_row("1", date(2026, 10, 8), 2.0, "x"), fetched_at=300.0)]) == 1
    assert store.latest_fetched_at("2026/2027") == 300.0
    latest = {r["understat_id"]: r for r in store.latest("2026/2027")}
    assert latest["1"]["day"] == date(2026, 10, 8) and latest["1"]["xg"] == 2.0
    assert latest["2"]["xg"] == 0.5 and latest["2"]["player_id"] is None
    assert [u["player_id"] for u in store.universe()] == ["x"]
