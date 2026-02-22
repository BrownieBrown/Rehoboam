"""Tests for expected points calculator — validates DGW multiplier and clamping."""

from rehoboam.expected_points import calculate_expected_points
from rehoboam.kickbase_client import MarketPlayer


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


class TestDGWMultiplier:
    """DGW multiplier should boost expected points by ~1.8x."""

    def test_dgw_boosts_expected_points(self):
        """is_dgw=True should increase expected_points by ~1.8x."""
        player = _make_player(average_points=15.0, points=100)

        normal = calculate_expected_points(player=player, is_dgw=False)
        dgw = calculate_expected_points(player=player, is_dgw=True)

        # DGW score should be ~1.8x normal score
        assert dgw.expected_points > normal.expected_points
        # Allow some tolerance for clamping, but ratio should be close to 1.8
        if normal.expected_points > 0:
            ratio = dgw.expected_points / normal.expected_points
            assert 1.7 <= ratio <= 1.81, f"Expected ~1.8x, got {ratio:.2f}x"

    def test_dgw_false_returns_normal_score(self):
        """is_dgw=False should return the same score as default (no DGW)."""
        player = _make_player(average_points=12.0, points=80)

        default = calculate_expected_points(player=player)
        explicit_false = calculate_expected_points(player=player, is_dgw=False)

        assert default.expected_points == explicit_false.expected_points

    def test_dgw_adds_note(self):
        """DGW should add 'DOUBLE GAMEWEEK' to notes."""
        player = _make_player(average_points=10.0, points=50)

        normal = calculate_expected_points(player=player, is_dgw=False)
        dgw = calculate_expected_points(player=player, is_dgw=True)

        assert "DOUBLE GAMEWEEK" not in normal.notes
        assert "DOUBLE GAMEWEEK" in dgw.notes

    def test_dgw_clamped_at_100(self):
        """Even with DGW multiplier, score should not exceed 100."""
        # Create a high-scoring player that would exceed 100 after 1.8x
        player = _make_player(average_points=20.0, points=200)

        # With starter bonus + good matchup, base could be 60+
        # 60 * 1.8 = 108 -> should clamp to 100
        dgw = calculate_expected_points(
            player=player,
            player_details={"prob": 1},  # Starter
            is_dgw=True,
        )

        assert dgw.expected_points <= 100

    def test_dgw_clamped_at_0(self):
        """Score should not go below 0 even with DGW on a negative-scoring player."""
        # Player with 0 average points and no data
        player = _make_player(average_points=0.0, points=0)

        dgw = calculate_expected_points(player=player, is_dgw=True)

        assert dgw.expected_points >= 0
