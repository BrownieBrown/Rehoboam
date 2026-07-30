"""Backtest replay harness.

Runs a scorer function across a sequence of matchdays and reports how well it
ranked players, using only data that existed before each matchday.

This is the *tuning* instrument: cheap, repeatable, safe to run hundreds of
times because it evaluates ranking on held-out data. It is deliberately
separate from the full-bot season replay (week 4), which is a *verdict*
instrument whose credibility decays every time it is tuned against.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rehoboam.backtest.metrics import lineup_regret, spearman
from rehoboam.backtest.snapshot import matches_before
from rehoboam.enrichment.corpus import TrainingCorpus

ScorerFn = Callable[[str, list[dict[str, Any]]], float]


@dataclass
class MatchdayInput:
    """One matchday to replay."""

    day_number: int
    squad: list[Any]
    actual_points: dict[str, float]


@dataclass
class MatchdayResult:
    day_number: int
    chosen_points: float
    best_points: float
    regret: float
    rank_correlation: float
    players_scored: int


@dataclass
class BacktestReport:
    results: list[MatchdayResult] = field(default_factory=list)
    mean_regret: float = 0.0
    mean_rank_correlation: float = 0.0
    total_chosen_points: float = 0.0
    total_best_points: float = 0.0


def run_backtest(
    corpus: TrainingCorpus,
    scorer_fn: ScorerFn,
    *,
    season: str,
    matchdays: list[MatchdayInput],
) -> BacktestReport:
    """Replay ``matchdays``, scoring each squad with ``scorer_fn``.

    For every player the scorer receives only ``matches_before`` the matchday
    under evaluation — the harness never hands it the answer.

    Args:
        corpus: source of per-player match history
        scorer_fn: ``(player_id, truncated_history) -> predicted_points``
        season: season being replayed, e.g. ``"2025/2026"``
        matchdays: matchdays to evaluate, in order

    Returns:
        A report with per-matchday detail and season aggregates.
    """
    from rehoboam.formation import select_best_eleven

    report = BacktestReport()
    if not matchdays:
        return report

    history_cache: dict[str, list[dict[str, Any]]] = {}

    for matchday in matchdays:
        predictions: dict[str, float] = {}
        for player in matchday.squad:
            if player.id not in history_cache:
                history_cache[player.id] = corpus.matches_for_player(player.id)
            visible = matches_before(
                history_cache[player.id], season=season, day_number=matchday.day_number
            )
            predictions[player.id] = scorer_fn(player.id, visible)

        chosen = select_best_eleven(matchday.squad, predictions)
        chosen_ids = [p.id for p in chosen]

        chosen_points = sum(matchday.actual_points.get(pid, 0.0) for pid in chosen_ids)
        regret = lineup_regret(matchday.squad, chosen_ids, matchday.actual_points)

        ids = [p.id for p in matchday.squad]
        correlation = spearman(
            [predictions[pid] for pid in ids],
            [matchday.actual_points.get(pid, 0.0) for pid in ids],
        )

        report.results.append(
            MatchdayResult(
                day_number=matchday.day_number,
                chosen_points=chosen_points,
                best_points=chosen_points + regret,
                regret=regret,
                rank_correlation=correlation,
                players_scored=len(predictions),
            )
        )

    n = len(report.results)
    report.mean_regret = sum(r.regret for r in report.results) / n
    report.mean_rank_correlation = sum(r.rank_correlation for r in report.results) / n
    report.total_chosen_points = sum(r.chosen_points for r in report.results)
    report.total_best_points = sum(r.best_points for r in report.results)
    return report
