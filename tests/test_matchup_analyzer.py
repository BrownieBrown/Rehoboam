"""Tests for MatchupAnalyzer — validates DGW detection from mdsum data."""

from rehoboam.matchup_analyzer import DoubleGameweekInfo, MatchupAnalyzer


class TestDetectDoubleGameweek:
    """detect_double_gameweek should identify when a team plays twice in one matchday."""

    def setup_method(self):
        self.analyzer = MatchupAnalyzer()

    def test_two_matches_same_matchday_is_dgw(self):
        """Two upcoming matches with the same mdid → is_dgw=True."""
        player_details = {
            "tid": "t1",
            "mdsum": [
                {"mdid": "md10", "mdst": 0, "t1": "t1", "t2": "t2", "md": "2025-03-01"},
                {"mdid": "md10", "mdst": 0, "t1": "t3", "t2": "t1", "md": "2025-03-04"},
                {"mdid": "md11", "mdst": 0, "t1": "t1", "t2": "t4", "md": "2025-03-08"},
            ],
        }

        result = self.analyzer.detect_double_gameweek(player_details)

        assert result.is_dgw is True
        assert result.dgw_matchday == "md10"
        assert result.match_count == 2

    def test_single_match_per_matchday_not_dgw(self):
        """One match per matchday → is_dgw=False."""
        player_details = {
            "tid": "t1",
            "mdsum": [
                {"mdid": "md10", "mdst": 0, "t1": "t1", "t2": "t2", "md": "2025-03-01"},
                {"mdid": "md11", "mdst": 0, "t1": "t3", "t2": "t1", "md": "2025-03-08"},
                {"mdid": "md12", "mdst": 0, "t1": "t1", "t2": "t4", "md": "2025-03-15"},
            ],
        }

        result = self.analyzer.detect_double_gameweek(player_details)

        assert result.is_dgw is False
        assert result.dgw_matchday is None
        assert result.match_count == 1

    def test_empty_mdsum_not_dgw(self):
        """Empty mdsum → is_dgw=False."""
        player_details = {"tid": "t1", "mdsum": []}

        result = self.analyzer.detect_double_gameweek(player_details)

        assert result.is_dgw is False

    def test_no_mdsum_key_not_dgw(self):
        """Missing mdsum key → is_dgw=False."""
        player_details = {"tid": "t1"}

        result = self.analyzer.detect_double_gameweek(player_details)

        assert result.is_dgw is False

    def test_only_played_matches_not_dgw(self):
        """All matches already played (mdst != 0) → is_dgw=False."""
        player_details = {
            "tid": "t1",
            "mdsum": [
                {"mdid": "md10", "mdst": 1, "t1": "t1", "t2": "t2", "md": "2025-02-01"},
                {"mdid": "md10", "mdst": 1, "t1": "t3", "t2": "t1", "md": "2025-02-04"},
            ],
        }

        result = self.analyzer.detect_double_gameweek(player_details)

        assert result.is_dgw is False

    def test_mixed_played_and_upcoming(self):
        """DGW only if the duplicated matchday is among upcoming (mdst=0) matches."""
        player_details = {
            "tid": "t1",
            "mdsum": [
                # Played match on md10
                {"mdid": "md10", "mdst": 1, "t1": "t1", "t2": "t2", "md": "2025-02-01"},
                # Upcoming match on md10 — but only 1 upcoming, so not a DGW
                {"mdid": "md10", "mdst": 0, "t1": "t3", "t2": "t1", "md": "2025-02-04"},
                # Upcoming match on md11
                {"mdid": "md11", "mdst": 0, "t1": "t1", "t2": "t4", "md": "2025-02-08"},
            ],
        }

        result = self.analyzer.detect_double_gameweek(player_details)

        # Only 1 upcoming match per matchday → not a DGW
        assert result.is_dgw is False

    def test_three_matches_same_matchday(self):
        """Three matches in one matchday (unlikely but valid) → is_dgw=True with count=3."""
        player_details = {
            "tid": "t1",
            "mdsum": [
                {"mdid": "md10", "mdst": 0, "t1": "t1", "t2": "t2", "md": "2025-03-01"},
                {"mdid": "md10", "mdst": 0, "t1": "t3", "t2": "t1", "md": "2025-03-02"},
                {"mdid": "md10", "mdst": 0, "t1": "t1", "t2": "t5", "md": "2025-03-03"},
            ],
        }

        result = self.analyzer.detect_double_gameweek(player_details)

        assert result.is_dgw is True
        assert result.match_count == 3

    def test_returns_dataclass(self):
        """Result should be a DoubleGameweekInfo dataclass."""
        player_details = {"tid": "t1", "mdsum": []}

        result = self.analyzer.detect_double_gameweek(player_details)

        assert isinstance(result, DoubleGameweekInfo)
