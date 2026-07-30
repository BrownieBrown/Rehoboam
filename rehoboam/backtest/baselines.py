"""Baseline models the v2 scorer has to beat.

The season-average baseline is one line of arithmetic and is a genuinely
strong predictor, because a player's own scoring rate is most of the signal.
The 2025/26 scorer would very likely have lost to it — which is exactly why
it exists here as the shipping fallback under the spec's safety valve.
"""

from __future__ import annotations

from typing import Any


def season_average_baseline(matches: list[dict[str, Any]]) -> float:
    """Mean points over matches the player actually appeared in.

    Absences (0 points *and* 0 minutes) are excluded rather than averaged in
    as zeros: an absence says nothing about how well the player performs, and
    counting it conflates "rotation risk" with "plays badly". Availability is
    modelled separately by the v2 scorer.

    Args:
        matches: rows from ``TrainingCorpus.matches_for_player``, already
            truncated by ``snapshot.matches_before``.

    Returns:
        Mean points per appearance; 0.0 when there are no appearances.
    """
    played = [m for m in matches if m["points"] != 0 or m["minutes"] > 0]
    if not played:
        return 0.0
    return sum(m["points"] for m in played) / len(played)
