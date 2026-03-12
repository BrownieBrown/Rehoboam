"""Pure scoring function for the EP-first pipeline.

score_player(PlayerData) -> PlayerScore — no API calls, no side effects.
"""

from .models import DataQuality, PlayerData, PlayerScore


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


def _extract_recent_avg(performance_data: dict | None, last_n: int = 5) -> float | None:
    """Extract average points from the last N matches where the player played."""
    if not performance_data:
        return None
    try:
        seasons = performance_data.get("it", [])
        if not seasons:
            return None
        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        matches = seasons_sorted[0].get("ph", [])
        played = [m for m in matches if not ("t" in m and m["t"] == 0 and m.get("p", 0) == 0)]
        if not played:
            return None
        recent = played[-last_n:]  # Last N matches
        return sum(m.get("p", 0) for m in recent) / len(recent)
    except Exception:
        return None


def score_player(data: PlayerData) -> PlayerScore:
    """Score a player based on expected matchday points.

    Pure function — no API calls, no side effects.
    Scale is 0-180 (not 0-100) to preserve DGW advantage.
    """
    player = data.player
    avg_points = player.average_points
    notes: list[str] = []

    # 1. Base points (0-40) — PRIMARY DRIVER
    base_points = min(avg_points * 2, 40)

    # 2. Consistency bonus (-5 to +15)
    games_played, consistency = extract_games_and_consistency(data.performance)

    consistency_bonus = 0.0
    if data.performance:
        if consistency >= 0.7:
            consistency_bonus = 15.0
            notes.append("Very consistent")
        elif consistency >= 0.3:
            consistency_bonus = consistency * 15
        else:
            consistency_bonus = -5.0
            notes.append("Inconsistent")

    # 3. Lineup probability (-20 to +20)
    lineup_bonus = 0.0
    has_lineup_data = False
    if data.player_details:
        has_lineup_data = True
        prob = data.player_details.get("prob", 5)
        if prob == 1:
            lineup_bonus = 20.0
            notes.append("Starter")
        elif prob == 2:
            lineup_bonus = 10.0
            notes.append("Rotation")
        elif prob == 3:
            lineup_bonus = 0.0
            notes.append("Bench")
        elif prob >= 4:
            lineup_bonus = -20.0
            notes.append("Unlikely to play")

    # 4. Fixture bonus (-10 to +15)
    fixture_bonus = 0.0
    has_fixture_data = data.team_strength is not None
    next_opponent = None

    if data.team_strength and data.opponent_strength:
        # Difficulty based on relative strength
        strength_diff = data.opponent_strength.strength_score - data.team_strength.strength_score
        raw_bonus = -strength_diff / 5  # Scale: 50pt diff -> ±10 bonus
        fixture_bonus = max(-10.0, min(15.0, raw_bonus))
        next_opponent = data.opponent_strength.team_name

        if fixture_bonus >= 5:
            notes.append("Easy fixture")
        elif fixture_bonus <= -5:
            notes.append("Hard fixture")

    # 5. Form bonus (-10 to +10) — recent matches vs season average
    form_bonus = 0.0
    recent_avg = _extract_recent_avg(data.performance, last_n=5)
    if recent_avg is not None and avg_points > 0:
        form_ratio = recent_avg / avg_points
        if form_ratio > 2.0:
            form_bonus = 10.0
            notes.append("Hot streak")
        elif form_ratio > 1.3:
            form_bonus = 5.0
        elif form_ratio < 0.5 and recent_avg > 0:
            form_bonus = -5.0
            notes.append("Below average")
        elif recent_avg == 0:
            form_bonus = -10.0
            notes.append("Not scoring")
    elif avg_points == 0:
        form_bonus = -10.0
        notes.append("Not scoring")

    # 6. Minutes bonus (-10 to +10)
    minutes_bonus = 0.0
    if data.performance:
        trend, avg_min, is_sub = extract_minutes_analysis(data.performance)
        if trend == "increasing":
            minutes_bonus = 10.0
            notes.append("Minutes increasing")
        elif trend == "decreasing":
            minutes_bonus = -10.0
            notes.append("Minutes decreasing")
        elif trend == "stable" and avg_min is not None and avg_min < 30:
            minutes_bonus = -8.0
            notes.append("Rarely plays")

    # Sum components
    raw_total = (
        base_points + consistency_bonus + lineup_bonus + fixture_bonus + form_bonus + minutes_bonus
    )

    # DGW multiplier
    dgw_multiplier = 1.8 if data.dgw_info.is_dgw else 1.0
    if data.dgw_info.is_dgw:
        notes.append("DOUBLE GAMEWEEK")

    total = raw_total * dgw_multiplier

    # Data quality grading
    data_quality = grade_data_quality(
        games_played=games_played,
        consistency=consistency,
        has_fixture_data=has_fixture_data,
        has_lineup_data=has_lineup_data,
    )

    # Grade F penalty: halve the score, but only when we have performance data
    # showing the player has played very few games. When there is no performance
    # data at all, grade F means "unknown" not "poor" — don't penalize.
    if data_quality.grade == "F" and data.performance is not None:
        total *= 0.5

    # Clamp to 0-180
    total = max(0, min(180, total))

    price = getattr(player, "price", player.market_value)
    status = getattr(player, "status", 0)  # Player (squad) has no status field

    return PlayerScore(
        player_id=player.id,
        expected_points=round(total, 1),
        data_quality=data_quality,
        base_points=base_points,
        consistency_bonus=round(consistency_bonus, 1),
        lineup_bonus=lineup_bonus,
        fixture_bonus=round(fixture_bonus, 1),
        form_bonus=form_bonus,
        minutes_bonus=minutes_bonus,
        dgw_multiplier=dgw_multiplier,
        is_dgw=data.dgw_info.is_dgw,
        next_opponent=next_opponent,
        notes=notes,
        current_price=price,
        market_value=player.market_value,
        position=player.position,
        average_points=avg_points,
        status=status,
        team_id=getattr(player, "team_id", ""),
    )
