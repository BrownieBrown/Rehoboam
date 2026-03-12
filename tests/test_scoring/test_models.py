"""Tests for scoring data models."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import DoubleGameweekInfo
from rehoboam.scoring.models import (
    BuyRecommendation,
    DataQuality,
    PlayerData,
    PlayerScore,
    TradePair,
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
            games_played=1,
            consistency=0.5,
            has_fixture_data=False,
            has_lineup_data=False,
            warnings=["Only 1 game played", "No fixture data"],
        )
        assert dq.grade == "F"
        assert len(dq.warnings) == 2


class TestPlayerScore:
    def test_construction(self):
        dq = DataQuality(
            grade="A",
            games_played=15,
            consistency=0.9,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        ps = PlayerScore(
            player_id="p1",
            expected_points=75.0,
            data_quality=dq,
            base_points=30.0,
            consistency_bonus=10.0,
            lineup_bonus=20.0,
            fixture_bonus=5.0,
            form_bonus=5.0,
            minutes_bonus=5.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent="Bayern",
            notes=["Starter"],
            current_price=5_000_000,
            market_value=6_000_000,
        )
        assert ps.expected_points == 75.0
        assert ps.is_dgw is False

    def test_dgw_player(self):
        dq = DataQuality(
            grade="B",
            games_played=8,
            consistency=0.6,
            has_fixture_data=True,
            has_lineup_data=False,
            warnings=[],
        )
        ps = PlayerScore(
            player_id="p2",
            expected_points=126.0,
            data_quality=dq,
            base_points=40.0,
            consistency_bonus=10.0,
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
        assert ps.expected_points == 126.0


class TestPlayerData:
    def test_construction_with_missing(self):
        player = _make_player()
        pd = PlayerData(
            player=player,
            performance=None,
            player_details=None,
            team_strength=None,
            opponent_strength=None,
            dgw_info=DoubleGameweekInfo(is_dgw=False),
            missing=["performance", "player_details"],
        )
        assert pd.performance is None
        assert "performance" in pd.missing


class TestBuyRecommendation:
    def test_construction(self):
        dq = DataQuality(
            grade="A",
            games_played=15,
            consistency=0.9,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        score = PlayerScore(
            player_id="p1",
            expected_points=70.0,
            data_quality=dq,
            base_points=30.0,
            consistency_bonus=10.0,
            lineup_bonus=20.0,
            fixture_bonus=5.0,
            form_bonus=5.0,
            minutes_bonus=0.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent="Dortmund",
            notes=[],
            current_price=5_000_000,
            market_value=5_000_000,
        )
        player = _make_player()
        rec = BuyRecommendation(player=player, score=score, roster_bonus=10.0, reason="fills_gap")
        assert rec.roster_bonus == 10.0
        assert rec.effective_ep == 80.0


class TestTradePair:
    def test_net_cost(self):
        dq = DataQuality(
            grade="A",
            games_played=15,
            consistency=0.9,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        )
        buy_score = PlayerScore(
            player_id="p1",
            expected_points=70.0,
            data_quality=dq,
            base_points=30.0,
            consistency_bonus=10.0,
            lineup_bonus=20.0,
            fixture_bonus=5.0,
            form_bonus=5.0,
            minutes_bonus=0.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent=None,
            notes=[],
            current_price=8_000_000,
            market_value=8_000_000,
        )
        sell_score = PlayerScore(
            player_id="p2",
            expected_points=30.0,
            data_quality=dq,
            base_points=15.0,
            consistency_bonus=5.0,
            lineup_bonus=10.0,
            fixture_bonus=0.0,
            form_bonus=0.0,
            minutes_bonus=0.0,
            dgw_multiplier=1.0,
            is_dgw=False,
            next_opponent=None,
            notes=[],
            current_price=3_000_000,
            market_value=3_000_000,
        )
        buy_player = _make_player(id="p1", price=8_000_000, market_value=8_000_000)
        sell_player = _make_player(id="p2", price=3_000_000, market_value=3_000_000)
        tp = TradePair(
            buy_player=buy_player,
            sell_player=sell_player,
            buy_score=buy_score,
            sell_score=sell_score,
        )
        assert tp.net_cost == 5_000_000
        assert tp.ep_gain == 40.0
