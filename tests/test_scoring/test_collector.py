"""Tests for DataCollector — assembles PlayerData from pre-fetched API data."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import MatchupAnalyzer
from rehoboam.scoring.collector import DataCollector


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


class TestDataCollector:
    def test_collect_with_all_data(self):
        """When all data is provided and no upcoming match exists, missing is empty."""
        collector = DataCollector(matchup_analyzer=MatchupAnalyzer())
        player = _make_player()
        # mdsum is empty list: no upcoming matches, so opponent_strength is not "missing"
        perf = {"m": [{"p": 10, "mp": 90}] * 5}
        details = {"prob": 1, "tid": "t1", "mdsum": []}

        result = collector.collect(
            player=player,
            performance=perf,
            player_details=details,
            team_profiles={},
        )

        assert result.player.id == "p1"
        assert result.performance == perf
        assert result.missing == []

    def test_collect_with_missing_performance(self):
        """When performance and player_details are None, both are flagged as missing."""
        collector = DataCollector(matchup_analyzer=MatchupAnalyzer())
        player = _make_player()

        result = collector.collect(
            player=player,
            performance=None,
            player_details=None,
            team_profiles={},
        )

        assert "performance" in result.missing
        assert "player_details" in result.missing

    def test_collect_flags_missing_opponent(self):
        """When there is an upcoming matchup but opponent not in team_profiles, opponent_strength is None."""
        collector = DataCollector(matchup_analyzer=MatchupAnalyzer())
        player = _make_player()
        # mdsum has one upcoming match (mdst=0) against team "t2"
        details = {
            "prob": 1,
            "tid": "t1",
            "mdsum": [{"mdst": 0, "t1": "t1", "t2": "t2", "md": "2026-03-22"}],
        }

        result = collector.collect(
            player=player,
            performance=None,
            player_details=details,
            team_profiles={},
        )

        assert result.opponent_strength is None

    def test_collect_resolves_team_strength(self):
        """When team_profiles contains both player's team and opponent, strengths are resolved."""
        collector = DataCollector(matchup_analyzer=MatchupAnalyzer())
        player = _make_player()
        details = {
            "prob": 1,
            "tid": "t1",
            "mdsum": [{"mdst": 0, "t1": "t1", "t2": "t2", "md": "2026-03-22"}],
        }
        team_profiles = {
            "t1": {"tid": "t1", "tn": "Test FC", "pl": 3, "tw": 10, "td": 2, "tl": 2},
            "t2": {"tid": "t2", "tn": "Opponent FC", "pl": 15, "tw": 2, "td": 3, "tl": 10},
        }

        result = collector.collect(
            player=player,
            performance={"m": []},
            player_details=details,
            team_profiles=team_profiles,
        )

        assert result.team_strength is not None
        assert result.team_strength.team_id == "t1"
        assert result.opponent_strength is not None
        assert result.opponent_strength.team_id == "t2"
        assert "opponent_strength" not in result.missing

    def test_collect_is_dgw_false_by_default(self):
        """DGW detection is a separate feature; is_dgw is always False from collector."""
        collector = DataCollector(matchup_analyzer=MatchupAnalyzer())
        player = _make_player()

        result = collector.collect(
            player=player,
            performance=None,
            player_details=None,
            team_profiles={},
        )

        assert result.is_dgw is False
