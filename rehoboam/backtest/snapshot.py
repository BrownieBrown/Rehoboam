"""Point-in-time truncation — the anti-leakage core of the harness.

Every scorer invocation during a backtest must go through this function.
Performance data as stored contains the whole season; scoring matchday 12
while able to see matchday 20 produces a model that looks brilliant offline
and fails live. That failure mode is the single biggest risk to the v2
validation effort, so the boundary is one small, heavily tested function.

Seasons are Kickbase ``ti`` titles in ``YYYY/YYYY`` form, which sort
correctly under plain lexicographic comparison ("2024/2025" < "2025/2026").
"""

from __future__ import annotations

from typing import Any


def matches_before(
    matches: list[dict[str, Any]], *, season: str, day_number: int
) -> list[dict[str, Any]]:
    """Matches strictly before ``(season, day_number)``.

    Includes every match from earlier seasons, and matches from ``season``
    with a lower ``day_number``. Excludes the cutoff matchday itself — when
    predicting matchday N, matchday N's result is exactly what we are not
    allowed to see.

    Args:
        matches: rows as returned by ``TrainingCorpus.matches_for_player``
        season: the season being predicted, e.g. ``"2025/2026"``
        day_number: the matchday being predicted

    Returns:
        Filtered list preserving input order.
    """
    result: list[dict[str, Any]] = []
    for match in matches:
        match_season = match["season"]
        if match_season < season:
            result.append(match)
        elif match_season == season and match["day_number"] < day_number:
            result.append(match)
    return result
