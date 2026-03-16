"""Pure EP scorer — no API calls, no side effects.

Receives PlayerData (assembled by DataCollector) and returns PlayerScore.
All scoring logic is encapsulated here; callers only need to pass data in
and read the result back out.
"""

from rehoboam.scoring.models import DataQuality, PlayerData, PlayerScore

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_consistency(performance: dict) -> tuple[int, float | None]:
    """Extract games played and consistency score from performance data.

    Parses ``performance["it"]`` — a list of season dicts each containing
    ``"ph"`` (list of match dicts with ``"p"`` = points and ``"t"`` =
    minutes).

    Returns:
        (games_played, consistency_score)
            games_played: number of matches the player actually appeared in
            consistency_score: 1 - CV  (0-1, where 1 = very consistent),
                               None when no data, 0.5 for a single game
    """
    try:
        seasons = performance.get("it", [])
        if not seasons:
            return 0, None

        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        current_season = seasons_sorted[0] if seasons_sorted else None
        if not current_season:
            return 0, None

        matches = current_season.get("ph", [])

        # Only count matches where the player actually appeared
        matches_played = [m for m in matches if m.get("p", 0) != 0 or m.get("t", 0) > 0]
        games_played = len(matches_played)

        if games_played == 0:
            return 0, None

        if games_played == 1:
            return 1, 0.5  # medium confidence for a single-game sample

        match_points = [m.get("p", 0) for m in matches_played]
        mean_pts = sum(match_points) / games_played

        if mean_pts == 0:
            return games_played, 0.0  # all zeros → no consistency signal

        variance = sum((p - mean_pts) ** 2 for p in match_points) / games_played
        std_dev = variance**0.5
        cv = std_dev / mean_pts

        # Convert CV to a 0-1 score (CV=0 → 1.0, CV≥2 → 0.0)
        consistency_score = max(0.0, 1.0 - cv / 2.0)
        return games_played, consistency_score

    except Exception:
        return 0, None


def _extract_minutes_trend(performance: dict) -> tuple[str | None, float | None]:
    """Compare first-half vs second-half of recent matches to derive a trend.

    Returns:
        (trend, avg_minutes)
            trend: "increasing" | "decreasing" | "stable" | None
            avg_minutes: average minutes per game, or None when unavailable
    """
    try:
        seasons = performance.get("it", [])
        if not seasons:
            return None, None

        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        current_season = seasons_sorted[0] if seasons_sorted else None
        if not current_season:
            return None, None

        matches = current_season.get("ph", [])
        minutes_data = [m["t"] for m in matches if "t" in m]

        if len(minutes_data) < 2:
            return None, None

        avg_minutes = sum(minutes_data) / len(minutes_data)

        if len(minutes_data) >= 4:
            half = len(minutes_data) // 2
            first_avg = sum(minutes_data[:half]) / half
            second_avg = sum(minutes_data[half:]) / (len(minutes_data) - half)
            diff_pct = ((second_avg - first_avg) / max(first_avg, 1)) * 100

            if diff_pct > 15:
                trend = "increasing"
            elif diff_pct < -15:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"

        return trend, avg_minutes

    except Exception:
        return None, None


def _grade_data_quality(
    games_played: int,
    has_fixture: bool,
    has_lineup: bool,
) -> DataQuality:
    """Grade data quality based on sample size and data availability.

    Grades:
        A — 10+ games played, fixture data present, lineup data present
        B — 5–9 games played, at least one of fixture/lineup present
        C — 2–4 games played, or both fixture and lineup missing
        F — 0–1 games played (score will be halved)
    """
    warnings: list[str] = []

    if not has_fixture:
        warnings.append("No fixture data")
    if not has_lineup:
        warnings.append("No lineup probability data")

    if games_played >= 10 and has_fixture and has_lineup:
        grade = "A"
    elif games_played >= 5 and (has_fixture or has_lineup):
        grade = "B"
    elif games_played >= 2:
        grade = "C"
        if not has_fixture and not has_lineup:
            warnings.append("Missing both fixture and lineup data")
    else:
        grade = "F"
        warnings.append("Insufficient games played for reliable estimate")

    return DataQuality(
        grade=grade,
        games_played=games_played,
        consistency=0.0,  # will be filled by score_player
        has_fixture_data=has_fixture,
        has_lineup_data=has_lineup,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------


def score_player(data: PlayerData) -> PlayerScore:
    """Score a player from assembled PlayerData.  Pure function — no I/O.

    Scoring bands (before DGW multiplier):
        base_points       0 – 40   (avg_points * 2, capped at 40)
        consistency_bonus -5 – +15
        lineup_bonus      -20 – +20
        fixture_bonus     -10 – +15
        minutes_bonus     -10 – +10
        form_bonus        -10 – +10
        ─────────────────────────────
        subtotal          ~-55 – 110  → clamped 0–100 pre-DGW
        DGW ×1.8          0 – 180
    """
    player = data.player
    avg_pts: float = player.average_points or 0.0
    current_pts: int = player.points or 0

    notes: list[str] = []

    # ------------------------------------------------------------------
    # 1. Base points  (0–40)
    # ------------------------------------------------------------------
    base_points = min(avg_pts * 2.0, 40.0)

    # ------------------------------------------------------------------
    # 2. Consistency bonus  (-5 to +15)
    # ------------------------------------------------------------------
    games_played, consistency = _extract_consistency(data.performance or {})

    consistency_bonus: float
    if consistency is None:
        consistency_bonus = 0.0
    elif consistency >= 0.7:
        consistency_bonus = 15.0
    elif consistency >= 0.3:
        consistency_bonus = consistency * 15.0
    else:
        consistency_bonus = -5.0

    # ------------------------------------------------------------------
    # 3. Lineup bonus  (-20 to +20)
    # ------------------------------------------------------------------
    lineup_bonus: float = 0.0
    has_lineup = False
    if data.player_details:
        prob = data.player_details.get("prob")
        has_lineup = prob is not None
        if prob == 1:
            lineup_bonus = 20.0
        elif prob == 2:
            lineup_bonus = 10.0
        elif prob == 3:
            lineup_bonus = 0.0
        elif prob is not None and prob >= 4:
            lineup_bonus = -20.0

    # ------------------------------------------------------------------
    # 4. Fixture bonus  (-10 to +15)
    # ------------------------------------------------------------------
    fixture_bonus: float = 0.0
    has_fixture = False
    next_opponent: str | None = None

    if data.team_strength and data.opponent_strength:
        has_fixture = True
        next_opponent = data.opponent_strength.team_name
        # Positive when player's team is stronger than the opponent
        diff = data.team_strength.strength_score - data.opponent_strength.strength_score
        # Map diff (-100 to +100) → bonus (-10 to +15)
        if diff >= 40:
            fixture_bonus = 15.0
        elif diff >= 20:
            fixture_bonus = 8.0
        elif diff >= 5:
            fixture_bonus = 3.0
        elif diff >= -5:
            fixture_bonus = 0.0
        elif diff >= -20:
            fixture_bonus = -5.0
        else:
            fixture_bonus = -10.0

    # ------------------------------------------------------------------
    # 5. Minutes trend bonus  (-10 to +10)
    # ------------------------------------------------------------------
    minutes_trend, avg_minutes = _extract_minutes_trend(data.performance or {})

    minutes_bonus: float = 0.0
    if minutes_trend == "increasing":
        minutes_bonus = 10.0
    elif minutes_trend == "decreasing":
        minutes_bonus = -10.0
    elif minutes_trend == "stable":
        if avg_minutes is not None and avg_minutes < 30:
            minutes_bonus = -8.0
        else:
            minutes_bonus = 0.0

    # ------------------------------------------------------------------
    # 6. Form bonus  (-10 to +10)
    # ------------------------------------------------------------------
    form_bonus: float = 0.0
    if avg_pts > 0:
        ratio = current_pts / avg_pts
        if ratio > 2.0:
            form_bonus = 10.0
        elif ratio > 1.3:
            form_bonus = 5.0
        elif ratio < 0.5:
            if current_pts == 0:
                form_bonus = -10.0
            else:
                form_bonus = -5.0
    else:
        if current_pts == 0:
            form_bonus = -10.0

    # ------------------------------------------------------------------
    # 7. Data quality grade
    # ------------------------------------------------------------------
    data_quality = _grade_data_quality(
        games_played=games_played,
        has_fixture=has_fixture,
        has_lineup=has_lineup,
    )
    # Back-fill the consistency field now that we have it
    data_quality.consistency = consistency if consistency is not None else 0.0

    # ------------------------------------------------------------------
    # 8. Assemble raw total and apply grade-F halving
    # ------------------------------------------------------------------
    raw_total = (
        base_points + consistency_bonus + lineup_bonus + fixture_bonus + minutes_bonus + form_bonus
    )

    if data_quality.grade == "F":
        raw_total = raw_total / 2.0
        notes.append("Score halved: insufficient data (grade F)")

    # Clamp pre-DGW score to 0-100
    pre_dgw = max(0.0, min(raw_total, 100.0))

    # ------------------------------------------------------------------
    # 9. DGW multiplier
    # ------------------------------------------------------------------
    dgw_multiplier = 1.8 if data.is_dgw else 1.0
    if data.is_dgw:
        notes.append("DOUBLE GAMEWEEK ×1.8")

    expected_points = min(pre_dgw * dgw_multiplier, 180.0)

    return PlayerScore(
        player_id=player.id,
        expected_points=round(expected_points, 2),
        data_quality=data_quality,
        base_points=base_points,
        consistency_bonus=consistency_bonus,
        lineup_bonus=lineup_bonus,
        fixture_bonus=fixture_bonus,
        form_bonus=form_bonus,
        minutes_bonus=minutes_bonus,
        dgw_multiplier=dgw_multiplier,
        is_dgw=data.is_dgw,
        next_opponent=next_opponent,
        notes=notes,
        current_price=player.price,
        market_value=player.market_value,
    )
