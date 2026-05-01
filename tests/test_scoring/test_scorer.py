"""Tests for the EP scorer — pure function, no API calls."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.scorer import (
    _extract_consistency,
    _extract_minutes_trend,
    _grade_data_quality,
    _parse_minutes,
    score_player,
)


def _make_player(**overrides) -> MarketPlayer:
    defaults = {
        "id": "p1",
        "first_name": "Test",
        "last_name": "Player",
        "position": "Midfielder",
        "team_id": "t1",
        "team_name": "Test FC",
        "price": 5_000_000,
        "market_value": 5_000_000,
        "points": 100,
        "average_points": 12.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


def _make_player_data(player=None, performance=None, player_details=None, **kw) -> PlayerData:
    if player is None:
        player = _make_player()
    return PlayerData(
        player=player,
        performance=performance,
        player_details=player_details,
        team_strength=None,
        opponent_strength=None,
        is_dgw=kw.get("is_dgw", False),
        missing=[],
    )


class TestBasePoints:
    def test_avg_20_gives_40_base(self):
        player = _make_player(average_points=20.0)
        result = score_player(_make_player_data(player=player))
        assert result.base_points == 40.0

    def test_avg_5_gives_10_base(self):
        player = _make_player(average_points=5.0)
        result = score_player(_make_player_data(player=player))
        assert result.base_points == 10.0

    def test_avg_0_gives_0_base(self):
        player = _make_player(average_points=0.0)
        result = score_player(_make_player_data(player=player))
        assert result.base_points == 0.0


class TestLineupBonus:
    def test_starter_gets_plus_20(self):
        player = _make_player()
        details = {"prob": 1}
        result = score_player(_make_player_data(player=player, player_details=details))
        assert result.lineup_bonus == 20.0

    def test_unlikely_gets_minus_20(self):
        player = _make_player()
        details = {"prob": 4}
        result = score_player(_make_player_data(player=player, player_details=details))
        assert result.lineup_bonus == -20.0

    def test_no_details_gets_0(self):
        player = _make_player()
        result = score_player(_make_player_data(player=player))
        assert result.lineup_bonus == 0.0


class TestFormBonus:
    def test_hot_streak(self):
        player = _make_player(average_points=10.0, points=25)
        result = score_player(_make_player_data(player=player))
        assert result.form_bonus == 10.0

    def test_not_scoring(self):
        player = _make_player(average_points=10.0, points=0)
        result = score_player(_make_player_data(player=player))
        assert result.form_bonus == -10.0


class TestPositionWeightedScoring:
    def _consistent_perf(self):
        """Performance data with very consistent output (high consistency)."""
        return {"it": [{"ti": "2024", "ph": [{"p": 20, "t": 90}] * 10}]}

    def _hot_streak_perf(self):
        """Recent 5 games >> season average."""
        # Season avg will be ~15; last 5 games avg = 30 → form_ratio = 2.0
        matches = [{"p": 5, "t": 90}] * 5 + [{"p": 30, "t": 90}] * 5
        return {"it": [{"ti": "2024", "ph": matches}]}

    def test_defender_rewards_consistency_more_than_forward(self):
        """Defender with high consistency scores higher consistency_bonus than forward."""
        perf = self._consistent_perf()
        def_player = _make_player(position="Defender", average_points=15.0)
        fwd_player = _make_player(position="Forward", average_points=15.0)

        def_score = score_player(_make_player_data(player=def_player, performance=perf))
        fwd_score = score_player(_make_player_data(player=fwd_player, performance=perf))

        assert def_score.consistency_bonus > fwd_score.consistency_bonus

    def test_forward_rewards_hot_streak_more_than_defender(self):
        """Forward on a hot streak scores higher form_bonus than a defender on same streak."""
        perf = self._hot_streak_perf()
        # Use season avg 15 to match the perf data (5*5 + 30*5) / 10 = 17.5
        def_player = _make_player(position="Defender", average_points=17.5)
        fwd_player = _make_player(position="Forward", average_points=17.5)

        def_score = score_player(_make_player_data(player=def_player, performance=perf))
        fwd_score = score_player(_make_player_data(player=fwd_player, performance=perf))

        assert fwd_score.form_bonus > def_score.form_bonus


class TestInjuryPenalty:
    def test_healthy_player_no_penalty(self):
        player = _make_player()
        details = {"prob": 1, "st": 0}
        result = score_player(_make_player_data(player=player, player_details=details))
        # No penalty note should appear
        assert not any("injury" in n.lower() or "uncertain" in n.lower() for n in result.notes)

    def test_long_term_injury_large_penalty(self):
        """Player with status 256 gets -30 deducted from raw total."""
        player = _make_player(average_points=20.0)  # base_points=40 (capped)
        details_healthy = {"prob": 1, "st": 0}
        details_injured = {"prob": 1, "st": 256}

        healthy = score_player(_make_player_data(player=player, player_details=details_healthy))
        injured = score_player(_make_player_data(player=player, player_details=details_injured))

        # Long-term injury should push score down by 30 points (clamped at 0 min)
        assert injured.expected_points < healthy.expected_points
        assert any("Long-term injury" in n for n in injured.notes)

    def test_short_term_injury_medium_penalty(self):
        player = _make_player(average_points=20.0)
        details_healthy = {"prob": 1, "st": 0}
        details_injured = {"prob": 1, "st": 4}

        healthy = score_player(_make_player_data(player=player, player_details=details_healthy))
        injured = score_player(_make_player_data(player=player, player_details=details_injured))

        assert injured.expected_points < healthy.expected_points
        assert any("Injured" in n for n in injured.notes)

    def test_uncertain_status_small_penalty(self):
        player = _make_player(average_points=20.0)
        details_healthy = {"prob": 1, "st": 0}
        details_uncertain = {"prob": 1, "st": 2}

        healthy = score_player(_make_player_data(player=player, player_details=details_healthy))
        uncertain = score_player(_make_player_data(player=player, player_details=details_uncertain))

        assert uncertain.expected_points < healthy.expected_points
        assert any("uncertain" in n.lower() for n in uncertain.notes)


class TestDGW:
    def test_dgw_multiplies_score(self):
        player = _make_player(average_points=15.0)
        normal = score_player(_make_player_data(player=player, is_dgw=False))
        dgw = score_player(_make_player_data(player=player, is_dgw=True))
        assert dgw.expected_points > normal.expected_points
        assert dgw.dgw_multiplier == 1.8

    def test_dgw_clamped_to_180(self):
        player = _make_player(average_points=100.0, points=200)
        result = score_player(_make_player_data(player=player, is_dgw=True))
        assert result.expected_points <= 180.0


class TestDataQualityGrading:
    def test_grade_a(self):
        grade = _grade_data_quality(games_played=12, has_fixture=True, has_lineup=True)
        assert grade.grade == "A"

    def test_grade_f_halves_score(self):
        # points == average_points → form_bonus = 0, no performance data → consistency_bonus = 0
        # base_points = min(20*2, 40) = 40 → halved = 20
        player = _make_player(average_points=20.0, points=20)
        result = score_player(_make_player_data(player=player))
        assert result.data_quality.grade == "F"
        assert result.expected_points == 20.0


class TestTotalScore:
    def test_score_clamped_to_0(self):
        player = _make_player(average_points=0.0, points=0)
        details = {"prob": 5}
        result = score_player(_make_player_data(player=player, player_details=details))
        assert result.expected_points >= 0.0


class TestExtractConsistency:
    def _make_perf(self, matches):
        """Build a performance dict in the format the API returns."""
        return {"it": [{"ti": "2024", "ph": matches}]}

    def test_no_performance_returns_zero(self):
        games, consistency = _extract_consistency({})
        assert games == 0
        assert consistency is None

    def test_single_game(self):
        perf = self._make_perf([{"p": 30, "mp": "90'"}])
        games, consistency = _extract_consistency(perf)
        assert games == 1
        assert consistency == 0.5  # medium confidence

    def test_consistent_player(self):
        # All same points → CV=0 → consistency=1.0
        perf = self._make_perf([{"p": 20, "mp": "90'"}] * 10)
        games, consistency = _extract_consistency(perf)
        assert games == 10
        assert consistency == 1.0

    def test_zero_points_games_excluded(self):
        # Games where player didn't play (0 points, 0 minutes) should be excluded
        perf = self._make_perf(
            [
                {"p": 0, "mp": "0'"},  # didn't play
                {"p": 30, "mp": "90'"},
                {"p": 20, "mp": "90'"},
            ]
        )
        games, consistency = _extract_consistency(perf)
        assert games == 2

    def test_brief_cameo_with_zero_points_still_counts(self):
        # Player came in for 5 minutes, scored 0 — that IS playing.
        # Pre-fix this was excluded because the t-field filter degraded
        # to `p != 0`; with mp wired up, minutes>0 keeps the appearance.
        perf = self._make_perf(
            [
                {"p": 0, "mp": "5'"},
                {"p": 30, "mp": "90'"},
                {"p": 20, "mp": "90'"},
            ]
        )
        games, _ = _extract_consistency(perf)
        assert games == 3


class TestExtractMinutesTrend:
    def _make_perf(self, matches):
        return {"it": [{"ti": "2024", "ph": matches}]}

    def test_no_performance_returns_none(self):
        trend, avg = _extract_minutes_trend({})
        assert trend is None
        assert avg is None

    def test_increasing_minutes(self):
        # First half low, second half high
        matches = [{"mp": "30'"}] * 4 + [{"mp": "90'"}] * 4
        perf = self._make_perf(matches)
        trend, avg = _extract_minutes_trend(perf)
        assert trend == "increasing"

    def test_decreasing_minutes(self):
        matches = [{"mp": "90'"}] * 4 + [{"mp": "20'"}] * 4
        perf = self._make_perf(matches)
        trend, avg = _extract_minutes_trend(perf)
        assert trend == "decreasing"

    def test_stable_minutes(self):
        matches = [{"mp": "75'"}] * 8
        perf = self._make_perf(matches)
        trend, avg = _extract_minutes_trend(perf)
        assert trend == "stable"
        assert avg == 75.0

    def test_missing_mp_field_skipped(self):
        # Future/scheduled matches in the API response have no `mp` —
        # treat them as "no data" rather than letting them crash or
        # falsely register as 0-minute appearances.
        matches = [{"mp": "85'"}] * 4 + [{}, {"mp": "85'"}] + [{"mp": "85'"}] * 3
        perf = self._make_perf(matches)
        trend, avg = _extract_minutes_trend(perf)
        # 8 valid minutes entries → still derives a trend
        assert trend == "stable"
        assert avg == 85.0


class TestParseMinutes:
    """The Kickbase API ships minutes as `mp` strings like `"13'"`.
    Anything else we encounter should degrade to 0 rather than crash —
    a single malformed match must not poison the whole player score.
    """

    def test_parses_apostrophe_suffix(self):
        assert _parse_minutes("13'") == 13
        assert _parse_minutes("90'") == 90
        assert _parse_minutes("100'") == 100

    def test_zero_minutes(self):
        assert _parse_minutes("0'") == 0

    def test_missing_returns_zero(self):
        assert _parse_minutes(None) == 0

    def test_empty_string_returns_zero(self):
        assert _parse_minutes("") == 0

    def test_unexpected_format_returns_zero(self):
        # Whatever Kickbase ships in extra time / abandoned matches —
        # don't crash, don't guess.
        assert _parse_minutes("90+5'") == 0
        assert _parse_minutes("garbage") == 0

    def test_plain_int_string(self):
        # Defensive: if Kickbase ever drops the apostrophe.
        assert _parse_minutes("75") == 75
