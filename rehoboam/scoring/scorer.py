"""Pure scoring function for the EP-first pipeline.

score_player(PlayerData) -> PlayerScore — no API calls, no side effects.
"""

from .models import DataQuality


def extract_games_and_consistency(
    performance_data: dict | None,
) -> tuple[int, float]:
    """Extract games played and consistency score from performance data.

    Returns:
        (games_played, consistency_score 0-1 where 1 = very consistent)
    """
    if not performance_data:
        return 0, 0.0

    try:
        seasons = performance_data.get("it", [])
        if not seasons:
            return 0, 0.0

        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        current_season = seasons_sorted[0]
        matches = current_season.get("ph", [])

        # Only count matches where player actually played.
        # Exclude only when minutes are explicitly recorded as 0 (didn't play).
        # If no "t" key, assume the match counts (minutes not tracked).
        matches_played = [
            m for m in matches if not ("t" in m and m["t"] == 0 and m.get("p", 0) == 0)
        ]
        games_played = len(matches_played)

        if games_played == 0:
            return 0, 0.0
        if games_played == 1:
            return 1, 0.5

        match_points = [m.get("p", 0) for m in matches_played]
        mean_points = sum(match_points) / games_played
        if mean_points == 0:
            return games_played, 0.0

        variance = sum((p - mean_points) ** 2 for p in match_points) / games_played
        std_dev = variance**0.5
        cv = std_dev / mean_points if mean_points > 0 else 1.0

        # CV 0 = perfect consistency (1.0), CV 1.5+ = very inconsistent (0.0)
        consistency = max(0.0, 1.0 - (cv / 1.5))
        return games_played, consistency

    except Exception:
        return 0, 0.0


def extract_minutes_analysis(
    performance_data: dict | None,
) -> tuple[str | None, float | None, bool | None]:
    """Extract minutes trend, average minutes, and substitution pattern.

    Returns:
        (minutes_trend, avg_minutes, is_substitute_pattern)
    """
    if not performance_data:
        return None, None, None

    try:
        seasons = performance_data.get("it", [])
        if not seasons:
            return None, None, None

        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        current_season = seasons_sorted[0]
        matches = current_season.get("ph", [])

        minutes_data = [m.get("t") for m in matches if m.get("t") is not None]
        if len(minutes_data) < 2:
            return None, None, None

        avg_minutes = sum(minutes_data) / len(minutes_data)

        # Trend: compare first half vs second half
        minutes_trend = "stable"
        if len(minutes_data) >= 4:
            half = len(minutes_data) // 2
            first_avg = sum(minutes_data[:half]) / half
            second_avg = sum(minutes_data[half:]) / (len(minutes_data) - half)
            diff_pct = ((second_avg - first_avg) / max(first_avg, 1)) * 100

            if diff_pct > 15:
                minutes_trend = "increasing"
            elif diff_pct < -15:
                minutes_trend = "decreasing"

        # Substitute pattern
        is_sub = avg_minutes < 60
        if not is_sub and len(minutes_data) >= 3:
            variance = sum((m - avg_minutes) ** 2 for m in minutes_data) / len(minutes_data)
            if variance**0.5 > 25 and avg_minutes < 75:
                is_sub = True

        return minutes_trend, avg_minutes, is_sub

    except Exception:
        return None, None, None


def grade_data_quality(
    games_played: int,
    consistency: float,
    has_fixture_data: bool,
    has_lineup_data: bool,
) -> DataQuality:
    """Assign a data quality grade based on available data."""
    warnings = []

    if games_played == 0:
        warnings.append("No games played")
    elif games_played <= 1:
        warnings.append(f"Only {games_played} game played")
    elif games_played <= 4:
        warnings.append(f"Only {games_played} games played")

    if not has_fixture_data:
        warnings.append("No fixture data")
    if not has_lineup_data:
        warnings.append("No lineup data")

    # Grade assignment
    if games_played <= 1:
        grade = "F"
    elif games_played <= 4 or (not has_fixture_data and not has_lineup_data):
        grade = "C"
    elif games_played <= 9 or (has_fixture_data != has_lineup_data):
        # 5-9 games, or has only one of fixture/lineup
        grade = "B"
    else:
        # 10+ games with both fixture and lineup data
        grade = "A"

    return DataQuality(
        grade=grade,
        games_played=games_played,
        consistency=consistency,
        has_fixture_data=has_fixture_data,
        has_lineup_data=has_lineup_data,
        warnings=warnings,
    )
