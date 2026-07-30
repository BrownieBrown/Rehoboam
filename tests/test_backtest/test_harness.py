"""Tests for rehoboam.backtest.harness."""

from __future__ import annotations

import pytest

from rehoboam.backtest.harness import MatchdayInput, run_backtest
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.kickbase_client import Player


def _player(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=0.0,
    )


def _legal_squad() -> list[Player]:
    return (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 7)]
        + [_player(str(i), "Midfielder") for i in range(7, 12)]
        + [_player(str(i), "Forward") for i in range(12, 15)]
    )


def _corpus_with_history(tmp_path, squad, points_by_day: dict[int, int]) -> TrainingCorpus:
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    for p in squad:
        perf = {
            "it": [
                {
                    "ti": "2025/2026",
                    "ph": [
                        {"day": d, "p": pts, "mp": "90'", "t1": "1", "t2": "2"}
                        for d, pts in points_by_day.items()
                    ],
                }
            ]
        }
        corpus.record_match_history(p.id, "1", perf)
    return corpus


def test_backtest_produces_one_result_per_matchday(tmp_path):
    squad = _legal_squad()
    corpus = _corpus_with_history(tmp_path, squad, {1: 50, 2: 50, 3: 50})
    matchdays = [
        MatchdayInput(day_number=d, squad=squad, actual_points={p.id: 50.0 for p in squad})
        for d in (2, 3)
    ]

    report = run_backtest(corpus, lambda pid, hist: 1.0, season="2025/2026", matchdays=matchdays)

    assert [r.day_number for r in report.results] == [2, 3]
    assert report.mean_regret >= 0.0


def test_scorer_never_sees_the_matchday_being_predicted(tmp_path):
    """The leak guard at harness level: whatever history the scorer receives
    must contain no match at or beyond the cutoff."""
    squad = _legal_squad()
    corpus = _corpus_with_history(tmp_path, squad, dict.fromkeys(range(1, 11), 50))
    seen: list[int] = []

    def spy_scorer(player_id: str, history: list[dict]) -> float:
        seen.extend(m["day_number"] for m in history)
        return 1.0

    matchdays = [
        MatchdayInput(day_number=5, squad=squad, actual_points={p.id: 50.0 for p in squad})
    ]
    run_backtest(corpus, spy_scorer, season="2025/2026", matchdays=matchdays)

    assert seen, "scorer was never called"
    assert max(seen) < 5


def test_perfect_scorer_has_zero_regret(tmp_path):
    """A scorer that knows the actual points must field the optimal 11."""
    squad = _legal_squad()
    corpus = _corpus_with_history(tmp_path, squad, {1: 50})
    actual = {p.id: float(50 + int(p.id)) for p in squad}
    matchdays = [MatchdayInput(day_number=2, squad=squad, actual_points=actual)]

    report = run_backtest(
        corpus, lambda pid, hist: actual[pid], season="2025/2026", matchdays=matchdays
    )

    assert report.mean_regret == pytest.approx(0.0)
    assert report.results[0].rank_correlation == pytest.approx(1.0)


def test_empty_matchdays_returns_empty_report(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    report = run_backtest(corpus, lambda pid, hist: 1.0, season="2025/2026", matchdays=[])

    assert report.results == []
    assert report.mean_regret == 0.0
