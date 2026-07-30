"""Leak-free feature construction for the v2 scorer.

Every feature on a row describes matches *strictly before* that row's matchday.
This is the fitting-side equivalent of ``backtest.snapshot.matches_before``: if
it leaks, the model looks excellent offline and fails live, and nothing crashes
to tell you.

Season boundaries reset history deliberately — last May's minutes are not
evidence about this August's.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kickbase per-match status. 1 = not in squad, 3 = came on, 4 = unused sub,
# 5 = started. 0 / None means the fixture has not been played yet.
#
# NOTE: this is NOT the injury `st` from get_player_details, which is a live
# serving-time signal with no historical counterpart.
PLAYED_STATUSES: tuple[int, ...] = (1, 3, 4, 5)

ROLLING_WINDOW = 3


@dataclass(frozen=True)
class MatchRow:
    """One played match, as stored in the training corpus."""

    player_id: str
    season: str
    day_number: int
    status: int | None
    points: int
    minutes: int


@dataclass(frozen=True)
class FeatureRow:
    """One training example: features from the past, target from the present."""

    player_id: str
    season: str
    day_number: int
    prev_status: int | None
    rolling_minutes_3: float
    matches_seen: int
    target_status: int | None
    target_points: int


def build_feature_rows(matches: list[MatchRow]) -> list[FeatureRow]:
    """Turn one player's match history into training rows.

    Args:
        matches: that player's matches, any order. Rows whose ``status`` is not
            a played status are dropped — an unplayed fixture is not evidence.

    Returns:
        One row per played match, ordered by (season, day_number). Features are
        derived only from earlier matches within the same season.
    """
    played = [m for m in matches if m.status in PLAYED_STATUSES]
    played.sort(key=lambda m: (m.season, m.day_number))

    rows: list[FeatureRow] = []
    current_season: str | None = None
    history: list[MatchRow] = []

    for match in played:
        if match.season != current_season:
            current_season = match.season
            history = []

        window = history[-ROLLING_WINDOW:]
        rolling = sum(m.minutes for m in window) / len(window) if window else 0.0

        rows.append(
            FeatureRow(
                player_id=match.player_id,
                season=match.season,
                day_number=match.day_number,
                prev_status=history[-1].status if history else None,
                rolling_minutes_3=rolling,
                matches_seen=len(history),
                target_status=match.status,
                target_points=match.points,
            )
        )
        history.append(match)

    return rows
