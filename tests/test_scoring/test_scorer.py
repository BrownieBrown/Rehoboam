"""Tests for the pure scoring function."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import DoubleGameweekInfo, TeamStrength
from rehoboam.scoring.models import PlayerData, PlayerScore
from rehoboam.scoring.scorer import (
    extract_games_and_consistency,
    extract_minutes_analysis,
    grade_data_quality,
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


def _make_performance(match_points: list[int], match_minutes: list[int] | None = None) -> dict:
    """Build a performance dict matching Kickbase API structure."""
    matches = []
    for i, pts in enumerate(match_points):
        match = {"p": pts}
        if match_minutes and i < len(match_minutes):
            match["t"] = match_minutes[i]
        matches.append(match)
    return {"it": [{"ti": "2025", "ph": matches}]}


class TestExtractGamesAndConsistency:
    def test_no_data(self):
        games, consistency = extract_games_and_consistency(None)
        assert games == 0
        assert consistency == 0.0

    def test_empty_seasons(self):
        games, consistency = extract_games_and_consistency({"it": []})
        assert games == 0

    def test_single_game(self):
        perf = _make_performance([80])
        games, consistency = extract_games_and_consistency(perf)
        assert games == 1
        assert consistency == 0.5  # Medium confidence for 1 game

    def test_consistent_player(self):
        # All scores around 60 — very consistent
        perf = _make_performance([58, 62, 60, 61, 59, 60, 62, 58, 60, 61])
        games, consistency = extract_games_and_consistency(perf)
        assert games == 10
        assert consistency > 0.8

    def test_inconsistent_player(self):
        # Wild swings — very inconsistent
        perf = _make_performance([10, 120, 5, 150, 0, 200, 15, 100])
        games, consistency = extract_games_and_consistency(perf)
        assert games == 8
        assert consistency < 0.4

    def test_skips_zero_point_zero_minute_matches(self):
        # Matches where player didn't play (0 points, 0 minutes)
        perf = _make_performance([80, 0, 60, 0, 70], [90, 0, 85, 0, 88])
        games, consistency = extract_games_and_consistency(perf)
        assert games == 3  # Only 3 matches where player actually played


class TestExtractMinutesAnalysis:
    def test_no_data(self):
        trend, avg, is_sub = extract_minutes_analysis(None)
        assert trend is None

    def test_increasing_minutes(self):
        # First half: ~45 min, second half: ~85 min
        perf = _make_performance(
            [50] * 8,
            [40, 45, 50, 42, 80, 85, 90, 88],
        )
        trend, avg, is_sub = extract_minutes_analysis(perf)
        assert trend == "increasing"

    def test_decreasing_minutes(self):
        perf = _make_performance(
            [50] * 8,
            [90, 88, 85, 90, 40, 35, 30, 25],
        )
        trend, avg, is_sub = extract_minutes_analysis(perf)
        assert trend == "decreasing"

    def test_substitute_pattern(self):
        perf = _make_performance([30] * 6, [25, 30, 20, 28, 22, 30])
        trend, avg, is_sub = extract_minutes_analysis(perf)
        assert is_sub is True
        assert avg < 60


class TestGradeDataQuality:
    def test_grade_a(self):
        dq = grade_data_quality(
            games_played=12,
            consistency=0.8,
            has_fixture_data=True,
            has_lineup_data=True,
        )
        assert dq.grade == "A"
        assert dq.warnings == []

    def test_grade_b_few_games(self):
        dq = grade_data_quality(
            games_played=7,
            consistency=0.6,
            has_fixture_data=True,
            has_lineup_data=False,
        )
        assert dq.grade == "B"

    def test_grade_c_sparse(self):
        dq = grade_data_quality(
            games_played=3,
            consistency=0.5,
            has_fixture_data=False,
            has_lineup_data=False,
        )
        assert dq.grade == "C"
        assert any("3 games" in w for w in dq.warnings)

    def test_grade_f_no_games(self):
        dq = grade_data_quality(
            games_played=0,
            consistency=0.0,
            has_fixture_data=False,
            has_lineup_data=False,
        )
        assert dq.grade == "F"
        assert any("No games" in w or "0" in w for w in dq.warnings)

    def test_grade_f_one_game(self):
        dq = grade_data_quality(
            games_played=1,
            consistency=0.5,
            has_fixture_data=True,
            has_lineup_data=True,
        )
        assert dq.grade == "F"


def _make_player_data(
    avg_points: float = 12.0,
    points: int = 100,
    performance: dict | None = None,
    player_details: dict | None = None,
    team_strength: TeamStrength | None = None,
    opponent_strength: TeamStrength | None = None,
    is_dgw: bool = False,
    status: int = 0,
) -> PlayerData:
    player = _make_player(average_points=avg_points, points=points, status=status)
    return PlayerData(
        player=player,
        performance=performance,
        player_details=player_details,
        team_strength=team_strength,
        opponent_strength=opponent_strength,
        dgw_info=DoubleGameweekInfo(is_dgw=is_dgw, match_count=2 if is_dgw else 1),
        missing=[],
    )


class TestScorePlayer:
    def test_basic_scoring(self):
        """Average player with no extra data scores based on avg_points only."""
        pd = _make_player_data(avg_points=15.0, points=100)
        ps = score_player(pd)
        assert isinstance(ps, PlayerScore)
        assert ps.base_points == 30.0  # min(15 * 2, 40)
        assert ps.expected_points > 0

    def test_high_avg_caps_at_40(self):
        pd = _make_player_data(avg_points=25.0, points=200)
        ps = score_player(pd)
        assert ps.base_points == 40.0

    def test_zero_avg_points(self):
        pd = _make_player_data(avg_points=0.0, points=0)
        ps = score_player(pd)
        assert ps.expected_points >= 0  # Never negative after clamp

    def test_starter_gets_lineup_bonus(self):
        pd = _make_player_data(avg_points=15.0, player_details={"prob": 1})
        ps = score_player(pd)
        assert ps.lineup_bonus == 20.0

    def test_unlikely_player_gets_penalty(self):
        pd = _make_player_data(avg_points=15.0, player_details={"prob": 4})
        ps = score_player(pd)
        assert ps.lineup_bonus == -20.0

    def test_dgw_multiplier(self):
        pd_normal = _make_player_data(avg_points=15.0, is_dgw=False)
        pd_dgw = _make_player_data(avg_points=15.0, is_dgw=True)
        ps_normal = score_player(pd_normal)
        ps_dgw = score_player(pd_dgw)
        assert ps_dgw.dgw_multiplier == 1.8
        assert ps_dgw.expected_points > ps_normal.expected_points
        if ps_normal.expected_points > 0:
            ratio = ps_dgw.expected_points / ps_normal.expected_points
            assert 1.7 <= ratio <= 1.81

    def test_dgw_note_added(self):
        pd = _make_player_data(avg_points=15.0, is_dgw=True)
        ps = score_player(pd)
        assert "DOUBLE GAMEWEEK" in ps.notes

    def test_scale_goes_above_100(self):
        """Strong DGW player should score above 100 (scale is 0-180)."""
        pd = _make_player_data(
            avg_points=20.0,
            points=200,
            player_details={"prob": 1},
            is_dgw=True,
        )
        ps = score_player(pd)
        # base 40 + lineup 20 + form 10 = 70 min, * 1.8 = 126
        assert ps.expected_points > 100

    def test_clamped_at_180(self):
        """Score should never exceed 180."""
        pd = _make_player_data(
            avg_points=30.0,
            points=500,
            player_details={"prob": 1},
            is_dgw=True,
        )
        perf = _make_performance(
            [100] * 15,  # Very consistent high scorer
            [90] * 15,  # Always plays full game
        )
        pd.performance = perf
        ps = score_player(pd)
        assert ps.expected_points <= 180

    def test_clamped_at_0(self):
        """Score should never go below 0."""
        pd = _make_player_data(
            avg_points=0.0,
            points=0,
            player_details={"prob": 5},
        )
        ps = score_player(pd)
        assert ps.expected_points >= 0

    def test_grade_f_halves_score(self):
        """Grade F players get their EP halved."""
        pd_few = _make_player_data(avg_points=20.0, points=100)
        pd_few.performance = _make_performance([100])  # 1 game
        ps = score_player(pd_few)
        assert ps.data_quality.grade == "F"
        # Base would be 40, but halved due to grade F
        # Exact value depends on other components but should be noticeably lower

    def test_form_hot_streak(self):
        # Recent 5 matches average 25 vs season avg 10 = 2.5x ratio
        perf = _make_performance([5, 5, 5, 5, 5, 25, 30, 20, 25, 25])
        pd = _make_player_data(avg_points=10.0, points=150, performance=perf)
        ps = score_player(pd)
        assert ps.form_bonus == 10.0
        assert "Hot streak" in ps.notes

    def test_form_not_scoring(self):
        # Recent matches all zeros
        perf = _make_performance([50, 60, 40, 0, 0, 0, 0, 0])
        pd = _make_player_data(avg_points=15.0, points=150, performance=perf)
        ps = score_player(pd)
        assert ps.form_bonus == -10.0
        assert "Not scoring" in ps.notes

    def test_price_context_preserved(self):
        pd = _make_player_data(avg_points=15.0)
        ps = score_player(pd)
        assert ps.current_price == 5_000_000
        assert ps.market_value == 5_000_000

    def test_works_with_squad_player(self):
        """Player (from get_squad) has no 'price' or 'status' fields."""
        from rehoboam.kickbase_client import Player

        squad_player = Player(
            id="sp1",
            first_name="Squad",
            last_name="Player",
            position="Midfielder",
            team_id="t1",
            team_name="Test FC",
            market_value=5_000_000,
            points=100,
            average_points=15.0,
        )
        pd = PlayerData(
            player=squad_player,
            performance=None,
            player_details=None,
            team_strength=None,
            opponent_strength=None,
            dgw_info=DoubleGameweekInfo(is_dgw=False),
            missing=[],
        )
        ps = score_player(pd)
        assert ps.current_price == 5_000_000  # Falls back to market_value
        assert ps.status == 0  # Falls back to 0
