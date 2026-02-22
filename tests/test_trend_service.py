"""Tests for TrendService — the single source of truth for player market value trends."""

from datetime import date

import pytest

from rehoboam.services.trend_service import (
    MarketValueHistory,
    MarketValuePoint,
    TrendAnalysis,
    TrendService,
)


class TestTrendAnalysis:
    """Test TrendAnalysis dataclass."""

    def test_default_values(self):
        ta = TrendAnalysis()
        assert ta.has_data is False
        assert ta.trend == "unknown"
        assert ta.trend_7d_pct == 0.0
        assert ta.momentum == "neutral"
        assert ta.is_dip_in_uptrend is False

    def test_to_dict_has_all_keys(self):
        ta = TrendAnalysis(has_data=True, trend="rising", trend_7d_pct=5.0)
        d = ta.to_dict()
        assert d["has_data"] is True
        assert d["trend"] == "rising"
        assert d["trend_7d_pct"] == 5.0
        # Legacy compat keys
        assert "change_pct" in d
        assert "trend_pct" in d
        assert "long_term_pct" in d
        assert "reference_value" in d
        assert "price_low" in d
        assert "is_dip_in_uptrend" in d
        assert "is_secular_decline" in d

    def test_to_dict_legacy_aliases(self):
        ta = TrendAnalysis(
            has_data=True,
            trend_7d_pct=3.0,
            trend_14d_pct=5.0,
            trend_30d_pct=8.0,
        )
        d = ta.to_dict()
        assert d["change_pct"] == 3.0  # = trend_7d_pct
        assert d["trend_pct"] == 5.0  # = trend_14d_pct
        assert d["long_term_pct"] == 8.0  # = trend_30d_pct


class TestTrendServiceAnalyze:
    """Test the pure analysis function with synthetic data."""

    @staticmethod
    def _make_history(values: list[int], peak: int = 0, low: int = 0, trp: int = 0) -> dict:
        """Helper to create a history_data dict from a list of market values."""
        it_array = [{"dt": i, "mv": v} for i, v in enumerate(values)]
        return {
            "it": it_array,
            "hmv": peak or max(values) if values else 0,
            "lmv": low or min(values) if values else 0,
            "trp": trp,
        }

    def test_empty_data_returns_no_data(self):
        result = TrendService.analyze({}, 1000000)
        assert result.has_data is False
        assert result.trend == "unknown"

    def test_zero_market_value_returns_no_data(self):
        history = self._make_history([1000000, 1100000, 1200000])
        result = TrendService.analyze(history, 0)
        assert result.has_data is False

    def test_empty_it_array_returns_no_data(self):
        result = TrendService.analyze({"it": []}, 1000000)
        assert result.has_data is False

    def test_single_value_returns_no_data(self):
        result = TrendService.analyze({"it": [{"dt": 1, "mv": 1000000}]}, 1000000)
        assert result.has_data is False

    def test_rising_trend(self):
        """Rising 14d should produce trend='rising'."""
        # 20 data points, steadily rising by ~10% over 14 days
        values = [1_000_000 + i * 10_000 for i in range(20)]
        current = values[-1] + 50_000  # Even higher now
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        assert result.trend == "rising"
        assert result.trend_14d_pct > 5
        assert result.data_points == 20

    def test_falling_trend(self):
        """Falling 14d should produce trend='falling'."""
        # 20 data points, steadily falling
        values = [2_000_000 - i * 20_000 for i in range(20)]
        current = values[-1] - 50_000
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        assert result.trend == "falling"
        assert result.trend_14d_pct < -5

    def test_stable_trend(self):
        """Small changes over 14d should produce trend='stable'."""
        values = [1_000_000 + (i % 3) * 1000 for i in range(20)]
        current = 1_001_000
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        assert result.trend == "stable"
        assert abs(result.trend_14d_pct) < 5

    def test_dip_in_uptrend(self):
        """Rising 90d + rising 30d + dipping 7d = is_dip_in_uptrend."""
        # Build 100 data points: overall rising strongly
        base = 1_000_000
        values = []
        for i in range(100):
            # Rising ~50% over 100 days
            values.append(base + i * 5_000)

        # Last 7 days: dip of about 4%
        peak_val = values[-1]
        for i in range(7):
            values.append(int(peak_val * (1 - 0.005 * (i + 1))))

        current = int(peak_val * 0.96)  # 4% below recent peak
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        assert result.is_dip_in_uptrend is True
        assert result.trend_7d_pct < -3
        assert result.trend_30d_pct > 3

    def test_secular_decline(self):
        """Falling across all windows = is_secular_decline."""
        # 100 data points, declining more steeply (needs >3% 7d, >5% 30d, >8% 90d)
        base = 2_000_000
        values = [base - i * 8_000 for i in range(100)]
        current = values[-1] - 30_000  # Still falling
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        assert result.is_secular_decline is True
        assert result.trend_7d_pct < -3
        assert result.trend_30d_pct < -5
        assert result.trend_90d_pct < -8

    def test_recovery(self):
        """Rising 7d but still below avg_90d = is_recovery."""
        # Long decline then recent uptick
        base = 2_000_000
        values = []
        # 90 days of decline
        for i in range(90):
            values.append(base - i * 5_000)
        # Last 10 days: recovery
        bottom = values[-1]
        for i in range(10):
            values.append(bottom + i * 8_000)

        current = values[-1] + 10_000
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        assert result.is_recovery is True
        assert result.trend_7d_pct > 3
        assert current < result.avg_90d

    def test_at_peak(self):
        """Current at or near peak value = is_at_peak."""
        values = [1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000]
        current = 1_400_000
        history = self._make_history(values, peak=1_400_000)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        assert result.is_at_peak is True

    def test_at_trough(self):
        """Current at or near low value = is_at_trough."""
        values = [2_000_000, 1_500_000, 1_200_000, 1_100_000, 1_050_000]
        current = 1_050_000
        history = self._make_history(values, low=1_050_000)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        assert result.is_at_trough is True

    def test_yearly_range_position(self):
        """Range position 0=low, 1=high."""
        values = [500_000, 1_000_000, 1_500_000, 2_000_000]
        # Current at midpoint
        current = 1_250_000
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert 0.4 < result.yearly_range_position < 0.6

    def test_momentum_score_bounds(self):
        """Momentum score should be clamped to [-100, +100]."""
        # Extreme rise
        values = [100_000, 200_000]
        current = 500_000
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert -100 <= result.momentum_score <= 100

    def test_momentum_labels(self):
        """Test momentum label assignment."""
        # Strong upward
        values = [1_000_000 + i * 50_000 for i in range(20)]
        current = values[-1] + 100_000
        history = self._make_history(values)
        result = TrendService.analyze(history, current)
        assert result.momentum in ["strong_up", "up"]

        # Strong downward
        values = [2_000_000 - i * 50_000 for i in range(20)]
        current = values[-1] - 100_000
        history = self._make_history(values)
        result = TrendService.analyze(history, current)
        assert result.momentum in ["strong_down", "down"]

    def test_peak_and_low_from_api_metadata(self):
        """Peak/low should use API metadata (hmv/lmv) when available."""
        values = [1_000_000, 1_100_000, 1_200_000]
        # API says peak was higher than data
        history = self._make_history(values, peak=1_500_000, low=800_000)
        current = 1_200_000

        result = TrendService.analyze(history, current)
        assert result.peak_value == 1_500_000
        assert result.low_value == 800_000

    def test_vs_peak_pct(self):
        """vs_peak_pct should show how far current is from peak."""
        values = [1_000_000, 1_500_000, 1_200_000]
        current = 1_200_000
        history = self._make_history(values, peak=1_500_000)

        result = TrendService.analyze(history, current)
        assert result.vs_peak_pct < 0  # Below peak
        assert result.vs_peak_pct == pytest.approx(-20.0, abs=1.0)

    def test_multi_window_changes(self):
        """Different windows should show different change percentages."""
        # 100 data points with varying trend
        values = []
        # First 70 days: stable at 1M
        for _ in range(70):
            values.append(1_000_000)
        # Next 23 days: rising to 1.2M
        for i in range(23):
            values.append(1_000_000 + i * 8_696)
        # Last 7 days: falling slightly
        for i in range(7):
            values.append(1_200_000 - i * 5_000)

        current = 1_170_000
        history = self._make_history(values)

        result = TrendService.analyze(history, current)
        assert result.has_data is True
        # 7d should be falling (negative)
        assert result.trend_7d_pct < 0
        # 30d should be rising (positive)
        assert result.trend_30d_pct > 0
        # 90d should be roughly rising
        assert result.trend_90d_pct > 0


class TestTrendServiceGetPurchasePrice:
    """Test purchase price extraction."""

    def test_get_purchase_price_from_trp(self):
        """Should return trp from history data."""

        class MockCache:
            def get_cached_history(self, **kwargs):
                return {"it": [{"dt": 1, "mv": 100}], "trp": 5_000_000}

            def cache_history(self, **kwargs):
                pass

        class MockClient:
            pass

        service = TrendService(MockClient(), MockCache())
        price = service.get_purchase_price("player1", "league1")
        assert price == 5_000_000

    def test_get_purchase_price_no_data(self):
        """Should return None when no data."""

        class MockCache:
            def get_cached_history(self, **kwargs):
                return None

        class MockClient:
            def get_player_market_value_history_v2(self, **kwargs):
                raise Exception("API error")

        service = TrendService(MockClient(), MockCache())
        price = service.get_purchase_price("player1", "league1")
        assert price is None

    def test_get_purchase_price_zero_trp(self):
        """Should return None when trp is 0."""

        class MockCache:
            def get_cached_history(self, **kwargs):
                return {"it": [{"dt": 1, "mv": 100}], "trp": 0}

            def cache_history(self, **kwargs):
                pass

        class MockClient:
            pass

        service = TrendService(MockClient(), MockCache())
        price = service.get_purchase_price("player1", "league1")
        assert price is None


class TestParseHistory:
    """Test the pure parse_history static method."""

    def test_parse_history_with_synthetic_data(self):
        """parse_history should produce correct dates sorted ascending."""
        # dt values are days since epoch
        # 2024-01-01 = date.fromtimestamp(19723 * 86400) = date(2024, 1, 1)
        day_epoch_jan1 = 19723  # 2024-01-01
        history_data = {
            "it": [
                {"dt": day_epoch_jan1 + 2, "mv": 1_200_000},
                {"dt": day_epoch_jan1, "mv": 1_000_000},
                {"dt": day_epoch_jan1 + 1, "mv": 1_100_000},
            ],
            "hmv": 1_500_000,
            "lmv": 900_000,
            "trp": 800_000,
        }

        result = TrendService.parse_history("player1", history_data)

        assert isinstance(result, MarketValueHistory)
        assert result.player_id == "player1"
        assert len(result.points) == 3
        # Sorted ascending by date
        assert result.points[0].value == 1_000_000
        assert result.points[1].value == 1_100_000
        assert result.points[2].value == 1_200_000
        assert result.points[0].date < result.points[1].date < result.points[2].date
        # Metadata
        assert result.peak_value == 1_500_000
        assert result.low_value == 900_000
        assert result.purchase_price == 800_000

    def test_parse_history_dt_epoch_day_conversion(self):
        """dt * 86400 should produce correct date."""
        # 2024-01-01 is day 19723 since Unix epoch
        day_epoch = 19723
        history_data = {
            "it": [{"dt": day_epoch, "mv": 1_000_000}],
            "hmv": 1_000_000,
            "lmv": 1_000_000,
        }

        result = TrendService.parse_history("p1", history_data)
        assert len(result.points) == 1
        assert result.points[0].date == date(2024, 1, 1)
        assert result.points[0].value == 1_000_000

    def test_parse_history_empty_data(self):
        """Empty dict should produce empty points."""
        result = TrendService.parse_history("p1", {})
        assert result.player_id == "p1"
        assert result.points == []
        assert result.peak_value == 0
        assert result.purchase_price is None

    def test_parse_history_none_data(self):
        """None should produce empty points."""
        result = TrendService.parse_history("p1", None)
        assert result.points == []

    def test_parse_history_missing_it_array(self):
        """Missing 'it' key should produce empty points."""
        result = TrendService.parse_history("p1", {"hmv": 100, "lmv": 50})
        assert result.points == []
        assert result.peak_value == 100
        assert result.low_value == 50

    def test_parse_history_skips_zero_values(self):
        """Points with mv=0 or dt=0 should be skipped."""
        history_data = {
            "it": [
                {"dt": 19723, "mv": 0},
                {"dt": 0, "mv": 1_000_000},
                {"dt": 19724, "mv": 1_100_000},
            ],
            "hmv": 1_100_000,
            "lmv": 1_100_000,
        }
        result = TrendService.parse_history("p1", history_data)
        assert len(result.points) == 1
        assert result.points[0].value == 1_100_000

    def test_parse_history_no_purchase_price_when_trp_zero(self):
        """trp=0 should result in purchase_price=None."""
        history_data = {"it": [{"dt": 19723, "mv": 1_000_000}], "hmv": 0, "lmv": 0, "trp": 0}
        result = TrendService.parse_history("p1", history_data)
        assert result.purchase_price is None


class TestGetHistory:
    """Test get_history method with mocked cache/client."""

    def test_get_history_returns_market_value_history(self):
        """get_history should return MarketValueHistory from cached data."""

        class MockCache:
            def get_cached_history(self, **kwargs):
                return {
                    "it": [
                        {"dt": 19723, "mv": 1_000_000},
                        {"dt": 19724, "mv": 1_100_000},
                    ],
                    "hmv": 1_200_000,
                    "lmv": 900_000,
                    "trp": 500_000,
                }

            def cache_history(self, **kwargs):
                pass

        class MockClient:
            pass

        service = TrendService(MockClient(), MockCache())
        result = service.get_history("player1", "league1")

        assert isinstance(result, MarketValueHistory)
        assert result.player_id == "player1"
        assert len(result.points) == 2
        assert result.points[0].date == date(2024, 1, 1)
        assert result.points[0].value == 1_000_000
        assert result.points[1].value == 1_100_000
        assert result.peak_value == 1_200_000
        assert result.low_value == 900_000
        assert result.purchase_price == 500_000

    def test_get_history_no_data_returns_empty(self):
        """get_history with no cache/API data should return empty history."""

        class MockCache:
            def get_cached_history(self, **kwargs):
                return None

        class MockClient:
            def get_player_market_value_history_v2(self, **kwargs):
                raise Exception("API error")

        service = TrendService(MockClient(), MockCache())
        result = service.get_history("player1", "league1")

        assert isinstance(result, MarketValueHistory)
        assert result.player_id == "player1"
        assert result.points == []

    def test_get_history_fetches_from_api_on_cache_miss(self):
        """get_history should fetch from API when cache misses."""
        api_called = False

        class MockCache:
            def get_cached_history(self, **kwargs):
                return None

            def cache_history(self, **kwargs):
                pass

        class MockClient:
            def get_player_market_value_history_v2(self, **kwargs):
                nonlocal api_called
                api_called = True
                return {
                    "it": [{"dt": 19723, "mv": 2_000_000}],
                    "hmv": 2_000_000,
                    "lmv": 2_000_000,
                }

        service = TrendService(MockClient(), MockCache())
        result = service.get_history("player1")

        assert api_called
        assert len(result.points) == 1
        assert result.points[0].value == 2_000_000


class TestMarketValuePointDataclass:
    """Test MarketValuePoint and MarketValueHistory dataclasses."""

    def test_market_value_point_fields(self):
        pt = MarketValuePoint(date=date(2024, 6, 15), value=1_500_000)
        assert pt.date == date(2024, 6, 15)
        assert pt.value == 1_500_000

    def test_market_value_history_defaults(self):
        h = MarketValueHistory(player_id="abc")
        assert h.player_id == "abc"
        assert h.points == []
        assert h.peak_value == 0
        assert h.low_value == 0
        assert h.purchase_price is None
