"""Tests for rehoboam.enrichment.corpus — the v2 training corpus store."""

from __future__ import annotations

import sqlite3

from rehoboam.enrichment.corpus import TrainingCorpus


def _perf(season: str, matches: list[dict]) -> dict:
    return {"it": [{"ti": season, "ph": matches}]}


def test_upsert_players_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    players = [
        {
            "player_id": "1",
            "last_name": "Musiala",
            "position": "Midfielder",
            "team_id": "2",
            "market_value": 30_000_000,
            "average_points": 120.0,
        }
    ]

    assert corpus.upsert_players(players) == 1
    assert corpus.upsert_players(players) == 1
    assert corpus.players_needing_fetch("performance") == ["1"]


def test_record_match_history_parses_minutes_and_home_away(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "team_id": "3"}])

    perf = _perf(
        "2025/2026",
        [
            {
                "day": 21,
                "p": 17,
                "mp": "1'",
                "md": "2026-02-07T14:30:00Z",
                "t1": "11",
                "t2": "3",
            },
            {
                "day": 22,
                "p": 72,
                "mp": "90+5'",
                "md": "2026-02-13T19:30:00Z",
                "t1": "3",
                "t2": "18",
            },
        ],
    )
    assert corpus.record_match_history("1", "3", perf) == 2

    rows = corpus.matches_for_player("1")
    assert [r["day_number"] for r in rows] == [21, 22]
    assert rows[0]["minutes"] == 1
    assert rows[1]["minutes"] == 95  # stoppage time summed
    assert rows[0]["is_home"] == 0  # team 3 is t2 -> away
    assert rows[1]["is_home"] == 1  # team 3 is t1 -> home
    assert rows[0]["opponent_team_id"] == "11"
    assert rows[1]["opponent_team_id"] == "18"


def test_record_match_history_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    perf = _perf("2025/2026", [{"day": 1, "p": 50, "mp": "90'", "t1": "3", "t2": "4"}])

    corpus.record_match_history("1", "3", perf)
    corpus.record_match_history("1", "3", perf)
    assert len(corpus.matches_for_player("1")) == 1


def test_matches_without_day_number_are_skipped(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    perf = _perf("2025/2026", [{"p": 50, "mp": "90'"}, {"day": 2, "p": 60, "mp": "90'"}])
    assert corpus.record_match_history("1", None, perf) == 1


def test_record_mv_series_drops_sentinel_rows(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    history = {
        "it": [
            {"dt": 20000, "mv": 5_000_000},
            {"dt": 20001, "mv": 0},  # sentinel for newly-listed
            {"dt": 20002, "mv": 5_100_000},
        ]
    }
    assert corpus.record_mv_series("1", history) == 2


def test_record_mv_series_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    history = {"it": [{"dt": 20000, "mv": 5_000_000}]}

    corpus.record_mv_series("1", history)
    corpus.record_mv_series("1", history)

    with sqlite3.connect(corpus.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM mv_series").fetchone()[0]
    assert count == 1


def test_mark_fetched_removes_player_from_pending(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1"}, {"player_id": "2"}])

    corpus.mark_fetched("1", performance=True)
    assert corpus.players_needing_fetch("performance") == ["2"]
    # marking performance must not satisfy the mv sweep
    assert set(corpus.players_needing_fetch("mv")) == {"1", "2"}
