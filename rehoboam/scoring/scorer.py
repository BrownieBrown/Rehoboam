"""Pure EP scorer — no API calls, no side effects.

Receives PlayerData (assembled by DataCollector) and returns PlayerScore.
All scoring logic is encapsulated here; callers only need to pass data in
and read the result back out.
"""

from rehoboam.scoring.models import DataQuality, PlayerData, PlayerScore

# ---------------------------------------------------------------------------
# Position-specific scoring scales
# ---------------------------------------------------------------------------
# Defenders/GKs: clean-sheet + tackle points → reward consistency, shallow form
# Forwards:      goal-driven spikes → tolerate variance, reward hot streaks
# Midfielders:   balanced contribution → unchanged from original scale
#
# Each tuple is (max_bonus, min_penalty) for that component.

_CONSISTENCY_SCALE: dict[str, tuple[float, float]] = {
    "Defender": (15.0, -5.0),
    "Goalkeeper": (15.0, -5.0),
    "Forward": (8.0, -2.0),
}
_FORM_SCALE: dict[str, tuple[float, float]] = {
    "Defender": (7.0, 3.0),
    "Goalkeeper": (7.0, 3.0),
    "Forward": (14.0, 7.0),
}
_DEFAULT_CONSISTENCY: tuple[float, float] = (15.0, -5.0)
_DEFAULT_FORM: tuple[float, float] = (10.0, 5.0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_minutes(mp) -> int:
    """Parse Kickbase ``mp`` minutes-played values (e.g. ``"13'"``) to int.

    Kickbase ships minutes as a string with a trailing apostrophe.  Anything
    that doesn't match the expected pattern (None, empty string, future
    matches without minutes, extra-time formats we've never observed)
    degrades silently to 0 — a single oddly-formatted entry must not
    poison the whole player score.
    """
    if not mp:
        return 0
    s = str(mp).rstrip("'")
    try:
        return int(s)
    except ValueError:
        return 0


def _extract_consistency(performance: dict) -> tuple[int, float | None]:
    """Extract games played and consistency score from performance data.

    Parses ``performance["it"]`` — a list of season dicts each containing
    ``"ph"`` (list of match dicts with ``"p"`` = points and ``"mp"`` =
    minutes-played string).

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

        # Only count matches where the player actually appeared.  A 0-point
        # appearance with non-zero minutes is still a played game (think
        # late sub who didn't touch the ball) — keep those in the sample.
        matches_played = [
            m for m in matches if m.get("p", 0) != 0 or _parse_minutes(m.get("mp")) > 0
        ]
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
        minutes_data = [_parse_minutes(m["mp"]) for m in matches if "mp" in m]

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


def _extract_recent_form(performance: dict, window: int = 5) -> float | None:
    """Average points over the last *window* matches played.

    Returns:
        Average points over recent matches, or None if fewer than 2 matches.
    """
    try:
        seasons = performance.get("it", [])
        if not seasons:
            return None

        seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
        current_season = seasons_sorted[0] if seasons_sorted else None
        if not current_season:
            return None

        matches = current_season.get("ph", [])
        matches_played = [
            m for m in matches if m.get("p", 0) != 0 or _parse_minutes(m.get("mp")) > 0
        ]

        if len(matches_played) < 2:
            return None

        recent = matches_played[-window:]
        return sum(m.get("p", 0) for m in recent) / len(recent)
    except Exception:
        return None


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


def score_player(data: PlayerData, calibration_multiplier: float = 1.0) -> PlayerScore:
    """Score a player from assembled PlayerData.  Pure function — no I/O.

    Args:
        data: Assembled per-player data (already pre-fetched by DataCollector).
        calibration_multiplier: Position-level correction factor from
            BidLearner.get_position_calibration_multiplier(). When the scorer
            systematically over/under-predicts for a position (e.g. defenders
            scoring 20% more than we predict), this multiplier closes the gap.
            Default 1.0 (uncalibrated). Applied to the final EP alongside DGW.

    Scoring bands (before DGW / calibration multipliers):
        base_points       0 – 40    (avg_points * 2, capped at 40)
        consistency_bonus -5 – +15  (forwards: -2 – +8; see _CONSISTENCY_SCALE)
        lineup_bonus      -20 – +20
        fixture_bonus     -10 – +15
        minutes_bonus     -15 – +10
        form_bonus        -10 – +10 (forwards: up to +14; see _FORM_SCALE)
        status_penalty    -30 – 0   (injury/health penalty)
        ─────────────────────────────
        subtotal          ~-90 – 114  → clamped 0–100 pre-DGW
        DGW ×1.8          0 – 180
        calibration       ×0.5 – ×1.5 (final output still clamped to 0–180)
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
    # 2. Consistency bonus  (position-scaled; bounded by _CONSISTENCY_SCALE)
    #    Forwards score from goals — spike variance is their nature, so both
    #    the ceiling bonus and the floor penalty are softened for them.
    #    Defenders, GKs, and midfielders use the full ±15/-5 range.
    # ------------------------------------------------------------------
    games_played, consistency = _extract_consistency(data.performance or {})

    position = player.position or ""
    max_consistency, min_consistency = _CONSISTENCY_SCALE.get(position, _DEFAULT_CONSISTENCY)

    consistency_bonus: float
    if consistency is None:
        consistency_bonus = 0.0
    elif consistency >= 0.7:
        consistency_bonus = max_consistency
    elif consistency >= 0.3:
        consistency_bonus = consistency * max_consistency
    else:
        consistency_bonus = min_consistency

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
    #    Weighted average over next 5 fixtures: 40/25/15/12/8.
    #    Heavier weight on the next match (most certain), decaying to minor
    #    weight on 5th fixture. Falls back gracefully to fewer fixtures.
    # ------------------------------------------------------------------
    fixture_bonus: float = 0.0
    has_fixture = False
    next_opponent: str | None = None

    def _diff_to_bonus(diff: float) -> float:
        if diff >= 40:
            return 15.0
        elif diff >= 20:
            return 8.0
        elif diff >= 5:
            return 3.0
        elif diff >= -5:
            return 0.0
        elif diff >= -20:
            return -5.0
        else:
            return -10.0

    if data.team_strength and data.upcoming_opponent_strengths:
        has_fixture = True
        next_opponent = data.upcoming_opponent_strengths[0].team_name
        # Weighted average over next 5 matches, decayed: closer = heavier.
        # Weights sum to 1.0 and degrade gracefully if fewer fixtures exist.
        weights = [0.40, 0.25, 0.15, 0.12, 0.08]
        total_diff = 0.0
        total_weight = 0.0
        for i, opp in enumerate(data.upcoming_opponent_strengths[:5]):
            w = weights[i] if i < len(weights) else 0.0
            total_diff += w * (data.team_strength.strength_score - opp.strength_score)
            total_weight += w
        diff = total_diff / total_weight if total_weight > 0 else 0.0
        fixture_bonus = _diff_to_bonus(diff)

        # Flag a notably favorable or brutal run over the next 5 matches.
        num_fixtures = len(data.upcoming_opponent_strengths[:5])
        if num_fixtures >= 3:
            if diff >= 20:
                notes.append(
                    f"Favorable run: next {num_fixtures} fixtures avg "
                    f"{diff:+.0f} strength vs team"
                )
            elif diff <= -20:
                notes.append(
                    f"Tough run: next {num_fixtures} fixtures avg " f"{diff:+.0f} strength vs team"
                )
    elif data.team_strength and data.opponent_strength:
        # Fallback: single opponent (backward compat)
        has_fixture = True
        next_opponent = data.opponent_strength.team_name
        diff = data.team_strength.strength_score - data.opponent_strength.strength_score
        fixture_bonus = _diff_to_bonus(diff)

    # ------------------------------------------------------------------
    # 5. Minutes trend bonus  (-15 to +10)
    # ------------------------------------------------------------------
    minutes_trend, avg_minutes = _extract_minutes_trend(data.performance or {})

    minutes_bonus: float = 0.0
    if minutes_trend == "increasing":
        minutes_bonus = 10.0
    elif minutes_trend == "decreasing":
        if avg_minutes is not None and avg_minutes < 30:
            minutes_bonus = -15.0
            notes.append(f"Minutes collapsing: avg {avg_minutes:.0f}min, trend decreasing")
        else:
            minutes_bonus = -10.0
    elif minutes_trend == "stable":
        if avg_minutes is not None and avg_minutes < 30:
            minutes_bonus = -8.0
        else:
            minutes_bonus = 0.0

    # ------------------------------------------------------------------
    # 6. Form bonus  (position-scaled; bounded by _FORM_SCALE)
    #    Uses last-5-games average vs season average to detect streaks.
    #    Forwards on a hot streak are high-leverage (goal spikes compound),
    #    so they get a larger ceiling bonus. Defenders get a shallower ceiling
    #    since clean-sheet streaks are team-driven, not individual.
    #    Falls back to season ratio if per-match data is unavailable.
    # ------------------------------------------------------------------
    hot_bonus, warm_bonus = _FORM_SCALE.get(position, _DEFAULT_FORM)

    form_bonus: float = 0.0
    recent_avg = _extract_recent_form(data.performance or {}, window=5)

    if recent_avg is not None and avg_pts > 0:
        form_ratio = recent_avg / avg_pts
        if form_ratio > 1.5:
            form_bonus = hot_bonus
            notes.append(f"Hot streak: recent avg {recent_avg:.0f} vs season {avg_pts:.0f}")
        elif form_ratio > 1.15:
            form_bonus = warm_bonus
        elif form_ratio < 0.5:
            form_bonus = -10.0
            notes.append(f"Cold slump: recent avg {recent_avg:.0f} vs season {avg_pts:.0f}")
        elif form_ratio < 0.75:
            form_bonus = -5.0
    elif avg_pts > 0:
        # Fallback: season ratio when per-match data unavailable
        ratio = current_pts / avg_pts
        if ratio > 2.0:
            form_bonus = hot_bonus
        elif ratio > 1.3:
            form_bonus = warm_bonus
        elif ratio < 0.5:
            form_bonus = -10.0 if current_pts == 0 else -5.0
    else:
        if current_pts == 0:
            form_bonus = -10.0

    # ------------------------------------------------------------------
    # 7. Injury / status penalty  (-30 to 0)
    #    Kickbase status codes: 0=healthy, 2=uncertain, 4=short-term injury,
    #    256=long-term injury. Players flagged unavailable shouldn't score
    #    near healthy starters regardless of their other attributes.
    # ------------------------------------------------------------------
    status_penalty: float = 0.0
    if data.player_details:
        status_code = data.player_details.get("st", 0)
        if status_code == 256:
            status_penalty = -30.0
            notes.append("Long-term injury — score heavily penalized")
        elif status_code == 4:
            status_penalty = -20.0
            notes.append("Injured — score penalized")
        elif status_code == 2:
            status_penalty = -10.0
            notes.append("Status uncertain — score penalized")

    # ------------------------------------------------------------------
    # 8. Data quality grade
    # ------------------------------------------------------------------
    data_quality = _grade_data_quality(
        games_played=games_played,
        has_fixture=has_fixture,
        has_lineup=has_lineup,
    )
    # Back-fill the consistency field now that we have it
    data_quality.consistency = consistency if consistency is not None else 0.0

    # ------------------------------------------------------------------
    # 9. Assemble raw total and apply grade-F halving
    # ------------------------------------------------------------------
    raw_total = (
        base_points
        + consistency_bonus
        + lineup_bonus
        + fixture_bonus
        + minutes_bonus
        + form_bonus
        + status_penalty
    )

    if data_quality.grade == "F":
        raw_total = raw_total / 2.0
        notes.append("Score halved: insufficient data (grade F)")

    # Clamp pre-DGW score to 0-100
    pre_dgw = max(0.0, min(raw_total, 100.0))

    # ------------------------------------------------------------------
    # 10. DGW multiplier
    # ------------------------------------------------------------------
    dgw_multiplier = 1.8 if data.is_dgw else 1.0
    if data.is_dgw:
        notes.append("DOUBLE GAMEWEEK ×1.8")

    if calibration_multiplier != 1.0:
        notes.append(f"Position calibration ×{calibration_multiplier:.2f}")

    expected_points = min(pre_dgw * dgw_multiplier * calibration_multiplier, 180.0)

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
        current_price=getattr(player, "price", player.market_value),
        market_value=player.market_value,
        average_points=avg_pts,
        position=player.position or "",
        lineup_probability=data.player_details.get("prob") if data.player_details else None,
        minutes_trend=minutes_trend,
    )
