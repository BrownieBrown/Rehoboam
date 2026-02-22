"""Single source of truth for player market value trends.

Replaces all scattered trend implementations:
- value_history.py:get_trend_analysis() — crude 2-3 point comparison
- trader.py:_get_player_trend() — fetch with cache (timeframe=30, broken)
- trader.py:_fetch_player_trends() — batch fetch, no cache, inline 14d/30d calc
- trader.py:448-473 — inline trend extraction in display_compact_action_plan
- cli.py:354-386 and cli.py:422-455 — two identical 30-line inline blocks
"""

from dataclasses import dataclass, field
from datetime import date
from statistics import mean
from typing import Any


@dataclass
class MarketValuePoint:
    """Single daily market value data point."""

    date: date
    value: int  # Market value in euros


@dataclass
class MarketValueHistory:
    """Full daily market value history for a player."""

    player_id: str
    points: list[MarketValuePoint] = field(default_factory=list)  # Sorted ascending by date
    peak_value: int = 0  # Highest value (from API hmv)
    low_value: int = 0  # Lowest value (from API lmv)
    purchase_price: int | None = None  # trp from API


@dataclass
class TrendAnalysis:
    """Multi-window trend analysis from 365-day market value history."""

    has_data: bool = False
    trend: str = "unknown"  # "rising" / "falling" / "stable" / "unknown"

    # Multi-window % changes
    trend_7d_pct: float = 0.0
    trend_14d_pct: float = 0.0
    trend_30d_pct: float = 0.0
    trend_90d_pct: float = 0.0

    # Moving averages
    avg_7d: float = 0.0
    avg_30d: float = 0.0
    avg_90d: float = 0.0

    # Peak/low
    peak_value: int = 0
    low_value: int = 0
    current_value: int = 0
    vs_peak_pct: float = 0.0
    yearly_range_position: float = 0.5  # 0=low, 1=high

    # Intelligence
    momentum: str = "neutral"  # strong_up / up / neutral / down / strong_down
    momentum_score: float = 0.0  # -100 to +100
    is_dip_in_uptrend: bool = False
    is_secular_decline: bool = False
    is_recovery: bool = False
    is_at_peak: bool = False
    is_at_trough: bool = False
    data_points: int = 0
    history: list[MarketValuePoint] = field(default_factory=list)

    # Legacy compat aliases
    change_pct: float = 0.0  # = trend_7d_pct
    trend_pct: float = 0.0  # = trend_14d_pct
    long_term_pct: float = 0.0  # = trend_30d_pct
    reference_value: int = 0
    price_low: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for legacy consumers that expect trend dicts."""
        return {
            "has_data": self.has_data,
            "trend": self.trend,
            "trend_pct": self.trend_14d_pct,
            "trend_7d_pct": self.trend_7d_pct,
            "trend_14d_pct": self.trend_14d_pct,
            "trend_30d_pct": self.trend_30d_pct,
            "trend_90d_pct": self.trend_90d_pct,
            "long_term_pct": self.trend_30d_pct,
            "change_pct": self.trend_7d_pct,
            "peak_value": self.peak_value,
            "low_value": self.low_value,
            "current_value": self.current_value,
            "vs_peak_pct": self.vs_peak_pct,
            "yearly_range_position": self.yearly_range_position,
            "momentum": self.momentum,
            "momentum_score": self.momentum_score,
            "is_dip_in_uptrend": self.is_dip_in_uptrend,
            "is_secular_decline": self.is_secular_decline,
            "is_recovery": self.is_recovery,
            "is_at_peak": self.is_at_peak,
            "is_at_trough": self.is_at_trough,
            "data_points": self.data_points,
            "reference_value": self.reference_value,
            "price_low": self.price_low,
        }


class TrendService:
    """Single source of truth for player market value trends.

    Fetches 365-day history (cached 24h) and returns multi-window analysis.
    """

    def __init__(self, api_client, cache):
        """
        Args:
            api_client: KickbaseV4Client instance (has get_player_market_value_history_v2)
            cache: ValueHistoryCache instance for caching raw API data
        """
        self.client = api_client
        self.cache = cache

    def get_trend(self, player_id: str, market_value: int, league_id: str = "") -> TrendAnalysis:
        """Fetch 365-day history (cached 24h), return multi-window analysis.

        Args:
            player_id: Player ID
            market_value: Current market value of the player
            league_id: League ID for cache keying

        Returns:
            TrendAnalysis with all windows populated
        """
        history = self._get_raw_history(player_id, league_id)
        if not history:
            return TrendAnalysis()
        return self.analyze(history, market_value)

    def get_trends_batch(self, players: list, league_id: str = "") -> dict[str, TrendAnalysis]:
        """Fetch trends for multiple players.

        Args:
            players: List of player objects (need .id and .market_value)
            league_id: League ID for cache keying

        Returns:
            Dict mapping player_id -> TrendAnalysis
        """
        results = {}
        for player in players:
            results[player.id] = self.get_trend(player.id, player.market_value, league_id)
        return results

    def get_purchase_price(self, player_id: str, league_id: str = "") -> int | None:
        """Extract transfer price (trp) from cached history data.

        Args:
            player_id: Player ID
            league_id: League ID for cache keying

        Returns:
            Transfer/purchase price or None if not available
        """
        history = self._get_raw_history(player_id, league_id)
        if history:
            trp = history.get("trp", 0)
            return trp if trp else None
        return None

    def get_history(self, player_id: str, league_id: str = "") -> MarketValueHistory:
        """Get full daily market value time series (365-day, cached 24h).

        Returns clean typed data from the same cache used by get_trend().
        """
        raw = self._get_raw_history(player_id, league_id)
        if not raw:
            return MarketValueHistory(player_id=player_id)
        return self.parse_history(player_id, raw)

    @staticmethod
    def parse_history(player_id: str, history_data: dict) -> MarketValueHistory:
        """Pure function: raw API data -> MarketValueHistory. No I/O."""
        if not history_data:
            return MarketValueHistory(player_id=player_id)

        it_array = history_data.get("it", [])
        sorted_items = sorted(it_array, key=lambda x: x.get("dt", 0))

        points = []
        for item in sorted_items:
            dt = item.get("dt", 0)
            mv = item.get("mv", 0)
            if dt > 0 and mv > 0:
                points.append(MarketValuePoint(date=date.fromtimestamp(dt * 86400), value=int(mv)))

        peak_value = int(history_data.get("hmv", 0))
        low_value = int(history_data.get("lmv", 0))
        trp = int(history_data.get("trp", 0))

        return MarketValueHistory(
            player_id=player_id,
            points=points,
            peak_value=peak_value,
            low_value=low_value,
            purchase_price=trp if trp else None,
        )

    def _get_raw_history(self, player_id: str, league_id: str = "") -> dict | None:
        """Get raw history data, using cache or fetching from API.

        Uses timeframe=365 for comprehensive trend analysis.
        """
        # Check cache first (use timeframe=365 as cache key)
        cached = self.cache.get_cached_history(
            player_id=player_id,
            league_id=league_id,
            timeframe=365,
            max_age_hours=24,
        )
        if cached:
            return cached

        # Fetch from API
        try:
            history_data = self.client.get_player_market_value_history_v2(
                player_id=player_id, timeframe=365
            )

            # Cache the result
            self.cache.cache_history(
                player_id=player_id,
                league_id=league_id,
                timeframe=365,
                data=history_data,
            )

            return history_data
        except Exception:
            return None

    @staticmethod
    def analyze(history_data: dict, current_market_value: int) -> TrendAnalysis:
        """Pure function: raw API data -> TrendAnalysis. No I/O.

        Args:
            history_data: Raw API response from get_player_market_value_history_v2
            current_market_value: Current market value for the player

        Returns:
            TrendAnalysis with all fields populated
        """
        if not history_data or current_market_value == 0:
            return TrendAnalysis()

        # Extract and sort historical data points ascending by date
        it_array = history_data.get("it", [])
        if not it_array:
            return TrendAnalysis()

        sorted_items = sorted(it_array, key=lambda x: x.get("dt", 0))
        values = [item.get("mv", 0) for item in sorted_items if item.get("mv", 0) > 0]

        if len(values) < 2:
            return TrendAnalysis()

        # Use current market value as the latest data point
        current = current_market_value

        # Peak/low from API metadata + computed from data
        api_peak = history_data.get("hmv", 0)
        api_low = history_data.get("lmv", 0)
        data_peak = max(values) if values else 0
        data_low = min(values) if values else 0

        peak_value = max(api_peak, data_peak, current)
        low_value = min(v for v in [api_low, data_low, current] if v > 0)

        # Window changes: pct_change(values[-N], values[-1])
        def pct_change(old_val: int, new_val: int) -> float:
            if old_val <= 0:
                return 0.0
            return ((new_val - old_val) / old_val) * 100

        n = len(values)
        trend_7d = pct_change(values[-min(7, n)], current) if n >= 2 else 0.0
        trend_14d = pct_change(values[-min(14, n)], current) if n >= 2 else 0.0
        trend_30d = pct_change(values[-min(30, n)], current) if n >= 2 else 0.0
        trend_90d = pct_change(values[-min(90, n)], current) if n >= 2 else 0.0

        # Moving averages
        avg_7d = mean(values[-min(7, n) :]) if n >= 2 else float(current)
        avg_30d = mean(values[-min(30, n) :]) if n >= 2 else float(current)
        avg_90d = mean(values[-min(90, n) :]) if n >= 2 else float(current)

        # Yearly range position: 0=low, 1=high
        value_range = peak_value - low_value
        range_position = (current - low_value) / value_range if value_range > 0 else 0.5
        range_position = max(0.0, min(1.0, range_position))

        # Vs peak percentage
        vs_peak = pct_change(peak_value, current) if peak_value > 0 else 0.0

        # Momentum score: weighted combination of windows
        momentum_score = 0.4 * trend_7d + 0.3 * trend_14d + 0.2 * trend_30d + 0.1 * trend_90d
        momentum_score = max(-100.0, min(100.0, momentum_score))

        # Momentum label
        if momentum_score > 15:
            momentum = "strong_up"
        elif momentum_score > 5:
            momentum = "up"
        elif momentum_score < -15:
            momentum = "strong_down"
        elif momentum_score < -5:
            momentum = "down"
        else:
            momentum = "neutral"

        # Pattern flags
        is_dip_in_uptrend = trend_7d < -3 and trend_30d > 3 and (n < 90 or trend_90d > 3)
        is_secular_decline = trend_7d < -3 and trend_30d < -5 and (n < 90 or trend_90d < -8)
        is_recovery = trend_7d > 3 and current < avg_90d
        is_at_peak = current >= peak_value * 0.97
        is_at_trough = current <= low_value * 1.03

        # Overall trend direction (based on 14d)
        if trend_14d > 5:
            trend = "rising"
        elif trend_14d < -5:
            trend = "falling"
        else:
            trend = "stable"

        # Reference value for legacy compat
        reference_value = values[-1] if values else 0

        return TrendAnalysis(
            has_data=True,
            trend=trend,
            # Multi-window
            trend_7d_pct=round(trend_7d, 2),
            trend_14d_pct=round(trend_14d, 2),
            trend_30d_pct=round(trend_30d, 2),
            trend_90d_pct=round(trend_90d, 2),
            # Averages
            avg_7d=round(avg_7d, 2),
            avg_30d=round(avg_30d, 2),
            avg_90d=round(avg_90d, 2),
            # Peak/low
            peak_value=peak_value,
            low_value=low_value,
            current_value=current,
            vs_peak_pct=round(vs_peak, 2),
            yearly_range_position=round(range_position, 4),
            # Intelligence
            momentum=momentum,
            momentum_score=round(momentum_score, 2),
            is_dip_in_uptrend=is_dip_in_uptrend,
            is_secular_decline=is_secular_decline,
            is_recovery=is_recovery,
            is_at_peak=is_at_peak,
            is_at_trough=is_at_trough,
            data_points=n,
            # Legacy compat
            change_pct=round(trend_7d, 2),
            trend_pct=round(trend_14d, 2),
            long_term_pct=round(trend_30d, 2),
            reference_value=reference_value,
            price_low=low_value,
        )
