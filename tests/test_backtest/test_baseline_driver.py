"""Tests for rehoboam.backtest.baseline_driver — the committed driver behind
week 1's headline regret number."""

from __future__ import annotations

from rehoboam.backtest.baseline_driver import (
    MIN_USABLE_SQUAD_SIZE,
    build_matchday_inputs,
    run_baseline,
)
from rehoboam.bid_learner import BidLearner, FlipOutcome
from rehoboam.enrichment.corpus import TrainingCorpus

SEASON = "2025/2026"
DAY = 86400.0
# 2026-02-14T14:30:00Z, matching the matchday_date format produced by the
# teamcenter backfill.
MATCHDAY_TS = 1_771_079_400.0


def _learner(tmp_path) -> BidLearner:
    return BidLearner(db_path=tmp_path / "bid_learning.db")


def _record_lineup(learner: BidLearner, day_number: int, fielded_ids: list[str]) -> None:
    learner.record_matchday_lineup_result(
        league_id="1",
        day_number=day_number,
        matchday_date="2026-02-14T14:30:00Z",
        total_points=sum(range(len(fielded_ids))),
        lineup_player_ids=fielded_ids,
        lineup_count=len(fielded_ids),
    )


def _flip(pid: str, buy_offset_days: float, sell_offset_days: float) -> FlipOutcome:
    return FlipOutcome(
        player_id=pid,
        player_name=f"P{pid}",
        buy_price=1_000_000,
        sell_price=1_100_000,
        profit=100_000,
        profit_pct=10.0,
        hold_days=int(sell_offset_days - buy_offset_days),
        buy_date=MATCHDAY_TS + buy_offset_days * DAY,
        sell_date=MATCHDAY_TS + sell_offset_days * DAY,
    )


def _corpus_with_positions_and_points(
    tmp_path, positions: dict[str, str], points_by_player_and_day: dict[str, dict[int, int]]
) -> TrainingCorpus:
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": pid, "position": pos} for pid, pos in positions.items()])
    for pid, points_by_day in points_by_player_and_day.items():
        perf = {
            "it": [
                {
                    "ti": SEASON,
                    "ph": [
                        {"day": d, "p": pts, "mp": "90'", "t1": "1", "t2": "2"}
                        for d, pts in points_by_day.items()
                    ],
                }
            ]
        }
        corpus.record_match_history(pid, "1", perf)
    return corpus


def _legal_position_set(n_extra_mid: int = 0) -> dict[str, str]:
    """1 GK + 3 DEF + 2 MID + 1 FWD (=7) plus extra midfielders, ids '1'.."""
    positions = {"1": "Goalkeeper", "2": "Defender", "3": "Defender", "4": "Defender"}
    positions.update({"5": "Midfielder", "6": "Midfielder", "7": "Forward"})
    for n in range(8, 8 + n_extra_mid):
        positions[str(n)] = "Midfielder"
    return positions


def test_matchday_with_enough_players_is_usable(tmp_path):
    learner = _learner(tmp_path)
    fielded = list(_legal_position_set().keys())  # 7 players fielded
    _record_lineup(learner, day_number=10, fielded_ids=fielded)

    # Two bench players held via flip windows, bringing the squad to 9 --
    # still short of MIN_USABLE_SQUAD_SIZE, so add enough to clear it.
    extra_positions = _legal_position_set(n_extra_mid=5)
    for pid in list(extra_positions.keys())[7:]:
        learner.record_flip(_flip(pid, -5, 5))

    points = {pid: {10: 40} for pid in extra_positions}
    corpus = _corpus_with_positions_and_points(tmp_path, extra_positions, points)

    matchdays, stats = build_matchday_inputs(corpus, learner_db_path=learner.db_path, season=SEASON)

    assert stats.matchdays_total == 1
    assert stats.matchdays_usable == 1
    assert stats.matchdays_skipped_small_squad == 0
    assert len(matchdays) == 1
    assert matchdays[0].day_number == 10
    assert {p.id for p in matchdays[0].squad} == set(extra_positions.keys())
    assert matchdays[0].actual_points == dict.fromkeys(extra_positions, 40.0)


def test_matchday_below_min_usable_squad_size_is_skipped(tmp_path):
    learner = _learner(tmp_path)
    positions = _legal_position_set()  # exactly 7 players -- below the floor
    fielded = list(positions.keys())
    _record_lineup(learner, day_number=10, fielded_ids=fielded)

    assert len(positions) < MIN_USABLE_SQUAD_SIZE

    points = {pid: {10: 40} for pid in positions}
    corpus = _corpus_with_positions_and_points(tmp_path, positions, points)

    matchdays, stats = build_matchday_inputs(corpus, learner_db_path=learner.db_path, season=SEASON)

    assert matchdays == []
    assert stats.matchdays_skipped_small_squad == 1
    assert stats.matchdays_usable == 0


def test_players_with_unresolved_position_are_dropped_from_the_squad(tmp_path):
    learner = BidLearner(db_path=tmp_path / "ghost_bid_learning.db")
    positions = _legal_position_set(n_extra_mid=5)
    fielded = list(positions.keys())

    # One extra fielded id ("999") has no player_universe row at all --
    # unresolved -- and must not survive into the squad.
    _record_lineup(learner, day_number=10, fielded_ids=fielded + ["999"])

    points = {pid: {10: 40} for pid in positions}
    corpus = _corpus_with_positions_and_points(tmp_path, positions, points)

    matchdays, _stats = build_matchday_inputs(
        corpus, learner_db_path=learner.db_path, season=SEASON
    )

    assert "999" not in {p.id for p in matchdays[0].squad}
    assert "999" not in matchdays[0].actual_points


def test_max_squad_size_keeps_fielded_players_first(tmp_path):
    learner = _learner(tmp_path)
    positions = _legal_position_set(n_extra_mid=8)  # 15 players total
    fielded = list(positions.keys())[:7]
    bench = list(positions.keys())[7:]  # 8 bench players via flip holds
    _record_lineup(learner, day_number=10, fielded_ids=fielded)
    for offset, pid in enumerate(bench):
        # staggered buy dates so "most recently bought" has a clear order
        learner.record_flip(_flip(pid, -10 + offset, 10))

    points = {pid: {10: 40} for pid in positions}
    corpus = _corpus_with_positions_and_points(tmp_path, positions, points)

    matchdays, _stats = build_matchday_inputs(
        corpus, learner_db_path=learner.db_path, season=SEASON, max_squad_size=12
    )

    squad_ids = {p.id for p in matchdays[0].squad}
    assert len(squad_ids) == 12
    # All 7 fielded players survive the cap...
    assert set(fielded).issubset(squad_ids)
    # ...and the 5 extra slots go to the most-recently-bought bench players
    # (highest offset == latest buy_date among the staggered dates above).
    assert squad_ids - set(fielded) == set(bench[-5:])


def test_max_squad_size_none_means_uncapped(tmp_path):
    learner = _learner(tmp_path)
    positions = _legal_position_set(n_extra_mid=10)  # 17 players total
    fielded = list(positions.keys())[:7]
    bench = list(positions.keys())[7:]
    _record_lineup(learner, day_number=10, fielded_ids=fielded)
    for offset, pid in enumerate(bench):
        learner.record_flip(_flip(pid, -10 + offset, 10))

    points = {pid: {10: 40} for pid in positions}
    corpus = _corpus_with_positions_and_points(tmp_path, positions, points)

    matchdays, _stats = build_matchday_inputs(
        corpus, learner_db_path=learner.db_path, season=SEASON, max_squad_size=None
    )

    assert {p.id for p in matchdays[0].squad} == set(positions.keys())


def test_run_baseline_returns_report_and_stats(tmp_path):
    learner = _learner(tmp_path)
    positions = _legal_position_set(n_extra_mid=5)  # 12 players
    fielded = list(positions.keys())
    _record_lineup(learner, day_number=10, fielded_ids=fielded)

    points = {pid: {9: 30, 10: 40} for pid in positions}
    corpus_path = tmp_path / "corpus.db"
    _corpus_with_positions_and_points(tmp_path, positions, points)

    report, stats = run_baseline(
        learner_db_path=learner.db_path, corpus_db_path=corpus_path, season=SEASON
    )

    assert stats.matchdays_usable == 1
    assert len(report.results) == 1
    assert report.results[0].day_number == 10
    assert report.mean_regret >= 0.0
