"""Tests for the EP scorer — pure function, no API calls."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.scorer import (
    _extract_consistency,
    _extract_minutes_trend,
    _grade_data_quality,
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
        perf = self._make_perf([{"p": 30, "t": 90}])
        games, consistency = _extract_consistency(perf)
        assert games == 1
        assert consistency == 0.5  # medium confidence

    def test_consistent_player(self):
        # All same points → CV=0 → consistency=1.0
        perf = self._make_perf([{"p": 20, "t": 90}] * 10)
        games, consistency = _extract_consistency(perf)
        assert games == 10
        assert consistency == 1.0

    def test_zero_points_games_excluded(self):
        # Games where player didn't play (0 points, 0 minutes) should be excluded
        perf = self._make_perf(
            [
                {"p": 0, "t": 0},  # didn't play
                {"p": 30, "t": 90},
                {"p": 20, "t": 90},
            ]
        )
        games, consistency = _extract_consistency(perf)
        assert games == 2


class TestExtractMinutesTrend:
    def _make_perf(self, matches):
        return {"it": [{"ti": "2024", "ph": matches}]}

    def test_no_performance_returns_none(self):
        trend, avg = _extract_minutes_trend({})
        assert trend is None
        assert avg is None

    def test_increasing_minutes(self):
        # First half low, second half high
        matches = [{"t": 30}] * 4 + [{"t": 90}] * 4
        perf = self._make_perf(matches)
        trend, avg = _extract_minutes_trend(perf)
        assert trend == "increasing"

    def test_decreasing_minutes(self):
        matches = [{"t": 90}] * 4 + [{"t": 20}] * 4
        perf = self._make_perf(matches)
        trend, avg = _extract_minutes_trend(perf)
        assert trend == "decreasing"

    def test_stable_minutes(self):
        matches = [{"t": 75}] * 8
        perf = self._make_perf(matches)
        trend, avg = _extract_minutes_trend(perf)
        assert trend == "stable"
        assert avg == 75.0
