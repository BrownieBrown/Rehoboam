"""Tests for scoring data models."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import DataQuality, PlayerScore


def _make_player(**overrides) -> MarketPlayer:
    """Create a test MarketPlayer with sensible defaults."""
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


class TestDataQuality:
    def test_grade_a(self):
        dq = DataQuality(
            grade="A",
            games_played=12,
            consistency=0.8,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        assert dq.grade == "A"
        assert dq.warnings == []

    def test_grade_f_has_warnings(self):
        dq = DataQuality(
            grade="F",
            games_played=0,
            consistency=0.0,
            has_fixture_data=False,
            has_lineup_data=False,
            warnings=["No games played"],
        )
        assert dq.grade == "F"
        assert len(dq.warnings) == 1


class TestPlayerScore:
    def test_construction_with_all_fields(self):
        dq = DataQuality(
            grade="B",
            games_played=7,
            consistency=0.6,
            has_fixture_data=True,
            has_lineup_data=False,
            warnings=[],
        )
        ps = PlayerScore(
            player_id="p1",
            expected_points=55.0,
            data_quality=dq,
            base_points=24.0,
            consistency_bonus=9.0,
            lineup_bonus=10.0,
            fixture_bonus=5.0,
            form_bonus=2.0,
            minutes_bonus=5.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent="Bayern",
            notes=[],
            current_price=5_000_000,
            market_value=5_000_000,
        )
        assert ps.expected_points == 55.0
        assert ps.player_id == "p1"

    def test_dgw_player(self):
        dq = DataQuality(
            grade="A",
            games_played=15,
            consistency=0.9,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        ps = PlayerScore(
            player_id="p2",
            expected_points=126.0,
            data_quality=dq,
            base_points=40.0,
            consistency_bonus=13.5,
            lineup_bonus=20.0,
            fixture_bonus=0.0,
            form_bonus=0.0,
            minutes_bonus=0.0,
            dgw_multiplier=1.8,
            is_dgw=True,
            next_opponent=None,
            notes=["DOUBLE GAMEWEEK"],
            current_price=10_000_000,
            market_value=10_000_000,
        )
        assert ps.is_dgw is True
        assert ps.dgw_multiplier == 1.8
