"""Tests for DataCollector — assembles PlayerData from pre-fetched API data."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import MatchupAnalyzer
from rehoboam.scoring.collector import DataCollector
from rehoboam.scoring.models import PlayerData


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
    def setup_method(self):
        self.collector = DataCollector(matchup_analyzer=MatchupAnalyzer())

    def test_collect_with_all_data(self):
        player = _make_player()
        performance = {"it": [{"ti": "2025", "ph": [{"p": 80, "t": 90}]}]}
        player_details = {"prob": 1, "st": 0, "tid": "t1", "mdsum": []}
        team_profiles = {
            "t1": {"tid": "t1", "tn": "Test FC", "pl": 5, "tw": 10, "td": 3, "tl": 5},
        }

        pd = self.collector.collect(player, performance, player_details, team_profiles)
        assert isinstance(pd, PlayerData)
        assert pd.player.id == "p1"
        assert pd.performance is not None
        assert pd.player_details is not None
        assert (
            pd.missing == [] or "opponent_strength" in pd.missing
        )  # opponent may be missing without matchup data

    def test_collect_missing_performance(self):
        player = _make_player()
        pd = self.collector.collect(player, None, None, {})
        assert "performance" in pd.missing
        assert "player_details" in pd.missing

    def test_collect_detects_dgw(self):
        player = _make_player()
        # Two matches in same matchday = DGW
        player_details = {
            "prob": 1,
            "st": 0,
            "tid": "t1",
            "mdsum": [
                {"mdid": "md1", "mdst": 0, "t1": "t1", "t2": "t2"},
                {"mdid": "md1", "mdst": 0, "t1": "t3", "t2": "t1"},
            ],
        }
        pd = self.collector.collect(player, None, player_details, {})
        assert pd.dgw_info.is_dgw is True

    def test_collect_no_dgw(self):
        player = _make_player()
        player_details = {
            "prob": 1,
            "st": 0,
            "tid": "t1",
            "mdsum": [
                {"mdid": "md1", "mdst": 0, "t1": "t1", "t2": "t2"},
                {"mdid": "md2", "mdst": 0, "t1": "t3", "t2": "t1"},
            ],
        }
        pd = self.collector.collect(player, None, player_details, {})
        assert pd.dgw_info.is_dgw is False
