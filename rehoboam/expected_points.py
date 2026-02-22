"""Expected matchday points calculator for lineup optimization"""

from dataclasses import dataclass


@dataclass
class ExpectedPointsResult:
    """Expected points calculation result for a player"""

    player_id: str
    expected_points: float  # 0-100 scale
    avg_points: float
    consistency_score: float | None
    lineup_probability: int | None  # 1-5 (1=starter, 5=unlikely)
    matchup_bonus: float
    minutes_trend: str | None
    form_bonus: float
    notes: list[str]


def calculate_expected_points(
    player,
    performance_data: dict | None = None,
    matchup_context: dict | None = None,
    player_details: dict | None = None,
    is_dgw: bool = False,
) -> ExpectedPointsResult:
    """
    Calculate expected matchday points for a player.

    This prioritizes actual matchday point scoring potential,
    not market value gains.

    Scoring formula (0-100 scale):
    - Average points (0-40): Primary driver. min(avg_points * 2, 40)
    - Consistency bonus (0-15): Consistent performers rewarded
    - Lineup probability (0-20): Starters get full bonus
    - Fixture bonus (-10 to +15): Easy/hard opponent adjustment
    - Minutes trend (-10 to +10): Playing time trajectory
    - Form bonus (-10 to +10): Recent vs average performance

    Args:
        player: Player object with average_points, points, etc.
        performance_data: Match-by-match performance data
        matchup_context: Matchup and SOS data
        player_details: Player details with lineup probability (prob field)
    """
    from .value_calculator import PlayerValue

    avg_points = player.average_points
    current_points = player.points
    notes = []

    # 1. Average points (0-40) — PRIMARY DRIVER
    avg_component = min(avg_points * 2, 40)

    # 2. Consistency bonus (0-15)
    consistency_score = None
    consistency_bonus = 0.0
    if performance_data:
        games_played, consistency = PlayerValue._extract_games_and_consistency(performance_data)
        consistency_score = consistency
        if consistency is not None and consistency > 0.3:
            consistency_bonus = consistency * 15
            if consistency >= 0.7:
                notes.append("Very consistent")
        elif consistency is not None and consistency < 0.3:
            consistency_bonus = -5  # Penalty for very inconsistent
            notes.append("Inconsistent")

    # 3. Lineup probability (0-20)
    lineup_prob = None
    lineup_component = 0.0
    if player_details:
        lineup_prob = player_details.get("prob", 5)
        if lineup_prob == 1:  # Starter
            lineup_component = 20
            notes.append("Starter")
        elif lineup_prob == 2:  # Rotation
            lineup_component = 10
            notes.append("Rotation")
        elif lineup_prob == 3:  # Bench
            lineup_component = 0
            notes.append("Bench")
        elif lineup_prob >= 4:  # Unlikely
            lineup_component = -20
            notes.append("Unlikely to play")

    # 4. Fixture bonus (-10 to +15)
    matchup_bonus = 0.0
    if matchup_context and matchup_context.get("has_data"):
        matchup_bonus_data = matchup_context.get("matchup_bonus", {})
        bonus_points = matchup_bonus_data.get("bonus_points", 0)
        # Scale to our range
        matchup_bonus = max(-10, min(15, bonus_points))

        if bonus_points >= 5:
            notes.append("Easy fixture")
        elif bonus_points <= -5:
            notes.append("Hard fixture")

    # 5. Minutes trend (-10 to +10)
    minutes_trend = None
    minutes_component = 0.0
    if performance_data:
        trend, avg_minutes, is_sub = PlayerValue._extract_minutes_analysis(performance_data)
        minutes_trend = trend
        if trend == "increasing":
            minutes_component = 10
            notes.append("Minutes increasing")
        elif trend == "decreasing":
            minutes_component = -10
            notes.append("Minutes decreasing")
        elif trend == "stable" and avg_minutes and avg_minutes < 30:
            minutes_component = -8
            notes.append("Rarely plays")

    # 6. Form bonus (-10 to +10)
    form_bonus = 0.0
    if avg_points > 0:
        form_ratio = current_points / avg_points if avg_points > 0 else 0
        if form_ratio > 2.0:
            form_bonus = 10
            notes.append("Hot streak")
        elif form_ratio > 1.3:
            form_bonus = 5
        elif form_ratio < 0.5 and current_points > 0:
            form_bonus = -5
            notes.append("Below average")
        elif current_points == 0:
            form_bonus = -10
            notes.append("Not scoring")

    # Calculate total
    total = (
        avg_component
        + consistency_bonus
        + lineup_component
        + matchup_bonus
        + minutes_component
        + form_bonus
    )

    # DGW multiplier: player plays twice, ~1.8x expected output (not 2.0 — rotation/fatigue risk)
    if is_dgw:
        total *= 1.8
        notes.append("DOUBLE GAMEWEEK")

    # Clamp to 0-100
    total = max(0, min(100, total))

    return ExpectedPointsResult(
        player_id=player.id,
        expected_points=round(total, 1),
        avg_points=avg_points,
        consistency_score=consistency_score,
        lineup_probability=lineup_prob,
        matchup_bonus=matchup_bonus,
        minutes_trend=minutes_trend,
        form_bonus=form_bonus,
        notes=notes,
    )
