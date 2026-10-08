"""Understat payload → store rows; the refresh step's freshness gate."""

from __future__ import annotations

from datetime import date

from rehoboam.enrichment import understat
from rehoboam.enrichment.understat import (
    current_team_title,
    run_understat_refresh,
    season_start_year,
    understat_rows,
)

UNIVERSE = [
    {"player_id": "2746", "first_name": "", "last_name": "Schick", "team_id": "7"},
    {"player_id": "8329", "first_name": "", "last_name": "Olise", "team_id": "2"},
]

PAYLOAD = [
    {
        "id": "7700",
        "player_name": "Patrik Schick",
        "team_title": "Bayer Leverkusen",
        "games": "4",
        "time": "264",
        "goals": "4",
        "assists": "0",
        "shots": "17",
        "key_passes": "4",
        "npg": "3",
        "yellow_cards": "0",
        "red_cards": "0",
        "xG": "3.6869700253009796",
        "xA": "0.24567142501473427",
        "npxG": "2.9",
        "xGChain": "4.1",
        "xGBuildup": "0.5",
        "position": "F S",
    },
    {
        "id": "9001",
        "player_name": "Someone Else",
        "team_title": "Hamburger SV,Paderborn",
        "games": "1",
        "time": "",
        "xG": "",
    },
    {"id": "", "player_name": "no id"},
]


def test_season_start_year():
    assert season_start_year("2026/2027") == 2026


def test_current_team_title_is_the_last_club_listed():
    assert current_team_title("Hamburger SV,Paderborn") == "Paderborn"
    assert current_team_title("Freiburg") == "Freiburg"
    assert current_team_title(None) is None


def test_rows_are_typed_matched_and_keep_the_unmatched_by_name():
    rows, unmatched = understat_rows(
        PAYLOAD, season="2026/2027", day=date(2026, 10, 8), fetched_at=1.0, universe=UNIVERSE
    )
    assert [r["understat_id"] for r in rows] == ["7700", "9001"]
    schick = rows[0]
    assert schick["player_id"] == "2746"
    assert schick["games"] == 4 and schick["time_played"] == 264
    assert schick["xg"] == 3.6869700253009796
    assert schick["position"] == "F S"
    assert schick["season"] == "2026/2027" and schick["day"] == date(2026, 10, 8)
    other = rows[1]
    assert other["player_id"] is None
    assert other["time_played"] is None and other["xg"] is None
    assert unmatched == ["Someone Else (Paderborn)"]


class _Store:
    def __init__(self, last=None):
        self.last = last
        self.written = []

    def latest_fetched_at(self, season):
        return self.last

    def universe(self):
        return UNIVERSE

    def write(self, rows):
        self.written = rows
        return len(rows)


def test_refresh_skips_while_the_last_snapshot_is_fresh(monkeypatch):
    store = _Store(last=1_000_000.0)
    monkeypatch.setattr(understat, "fetch_player_stats", lambda *a, **k: 1 / 0)
    out = run_understat_refresh(
        store, season="2026/2027", now=1_000_000.0 + 3600, stale_after_s=86400
    )
    assert out["skipped"].startswith("fresh")
    assert out["written"] == 0 and out["error"] is None


def test_refresh_fetches_when_stale_and_counts_the_unmatched(monkeypatch):
    store = _Store(last=None)
    monkeypatch.setattr(understat, "fetch_player_stats", lambda *a, **k: PAYLOAD)
    out = run_understat_refresh(store, season="2026/2027", now=1_700_000_000.0, stale_after_s=86400)
    assert out == {"written": 2, "unmatched": 1, "skipped": None, "error": None}
    assert store.written[0]["player_id"] == "2746"


def test_refresh_never_raises(monkeypatch):
    store = _Store(last=None)

    def boom(*a, **k):
        raise RuntimeError("understat down")

    monkeypatch.setattr(understat, "fetch_player_stats", boom)
    out = run_understat_refresh(store, season="2026/2027", now=1.0, stale_after_s=0)
    assert out["error"] == "RuntimeError: understat down"
    assert out["written"] == 0
