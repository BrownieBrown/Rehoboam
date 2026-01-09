"""Historic player value tracking with API caching"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class ValueHistoryCache:
    """Caches player market value history from API to minimize calls"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            # Store in logs directory by default
            db_path = Path("logs") / "player_history.db"

        # Ensure logs directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_value_cache (
                    player_id TEXT NOT NULL,
                    league_id TEXT NOT NULL,
                    timeframe INTEGER NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (player_id, league_id, timeframe)
                )
            """
            )

            # Performance data cache
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_cache (
                    player_id TEXT NOT NULL,
                    league_id TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (player_id, league_id)
                )
            """
            )

            # Index for cleanup queries
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fetched_at
                ON market_value_cache(fetched_at)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perf_fetched_at
                ON performance_cache(fetched_at)
            """
            )

            conn.commit()

    def get_cached_history(
        self, player_id: str, league_id: str, timeframe: int = 30, max_age_hours: int = 24
    ) -> dict[str, Any] | None:
        """
        Get cached market value history if recent enough

        Args:
            player_id: Player ID
            league_id: League ID
            timeframe: Timeframe in days
            max_age_hours: Maximum age of cache in hours (default: 24)

        Returns:
            Cached data dict or None if not found/too old
        """
        cutoff = int((datetime.now() - timedelta(hours=max_age_hours)).timestamp())

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT data, fetched_at
                FROM market_value_cache
                WHERE player_id = ? AND league_id = ? AND timeframe = ? AND fetched_at >= ?
            """,
                (player_id, league_id, timeframe, cutoff),
            )

            row = cursor.fetchone()
            if row:
                return json.loads(row[0])

        return None

    def cache_history(self, player_id: str, league_id: str, timeframe: int, data: dict[str, Any]):
        """Cache market value history data"""
        timestamp = int(datetime.now().timestamp())

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_value_cache
                (player_id, league_id, timeframe, fetched_at, data)
                VALUES (?, ?, ?, ?, ?)
            """,
                (player_id, league_id, timeframe, timestamp, json.dumps(data)),
            )
            conn.commit()

    def get_trend_analysis(
        self, history_data: dict[str, Any], current_market_value: int = 0
    ) -> dict[str, Any]:
        """
        Analyze trend from API history data with focus on RECENT momentum

        CRITICAL: Use recent price movement to avoid buying at peaks!

        Args:
            history_data: Response from API get_player_market_value_history
            current_market_value: Current market value of player

        Returns:
            Trend analysis dict with:
            - trend: "rising", "falling", "stable", "unknown"
            - change_pct: percentage change (RECENT, not overall)
            - reference_value: reference price from API
            - current_value: current market value
        """
        # API Response structure:
        # {
        #   "it": [{dt: days_since_epoch, mv: market_value}, ...],  # Historical data points
        #   "trp": <number>,  # Reference price (could be old)
        #   "hmv": <number>,  # Highest market value (peak)
        #   "lmv": <number>,  # Lowest market value
        # }

        if not history_data or current_market_value == 0:
            return {"trend": "unknown", "change_pct": 0.0, "has_data": False}

        # CRITICAL FIX: Look at historical data points for RECENT trend
        historical_items = history_data.get("it", [])
        peak_value = history_data.get("hmv", 0)

        if historical_items and len(historical_items) >= 2:
            # Sort by date (most recent first)
            sorted_items = sorted(historical_items, key=lambda x: x.get("dt", 0), reverse=True)

            # Get most recent historical value (not current, but last recorded)
            # and compare to value from 7-14 days ago for RECENT trend
            recent_value = sorted_items[0].get("mv", 0) if len(sorted_items) > 0 else 0
            week_ago_value = (
                sorted_items[min(2, len(sorted_items) - 1)].get("mv", 0)
                if len(sorted_items) > 1
                else 0
            )

            # CRITICAL: Check for recent peak in last few data points
            # Look at last 3-4 data points to find local peak
            recent_peak = max(
                [item.get("mv", 0) for item in sorted_items[: min(4, len(sorted_items))]]
            )

            # Use RECENT trend (last week) instead of overall
            if week_ago_value > 0 and recent_value > 0:
                # Recent trend: comparing last data point to week ago
                recent_change_pct = ((recent_value - week_ago_value) / week_ago_value) * 100

                # Also check if currently ABOVE or BELOW recent value
                # If current < recent, they're falling RIGHT NOW
                current_vs_recent = (
                    ((current_market_value - recent_value) / recent_value) * 100
                    if recent_value > 0
                    else 0
                )

                # CRITICAL: Check if falling from recent peak (last 3-4 data points)
                if recent_peak > 0 and current_market_value < recent_peak * 0.92:
                    # More than 8% below recent peak = likely falling from peak
                    decline_from_peak = ((current_market_value - recent_peak) / recent_peak) * 100
                    return {
                        "trend": "falling",
                        "change_pct": round(decline_from_peak, 2),
                        "reference_value": recent_peak,
                        "price_low": week_ago_value,
                        "current_value": current_market_value,
                        "has_data": True,
                    }

                # REJECT if falling vs most recent data (catching knives)
                if current_vs_recent < -5:
                    trend = "falling"
                    change_pct = current_vs_recent
                # Otherwise use recent trend
                elif recent_change_pct > 5:
                    trend = "rising"
                    change_pct = recent_change_pct
                elif recent_change_pct < -5:
                    trend = "falling"
                    change_pct = recent_change_pct
                else:
                    trend = "stable"
                    change_pct = recent_change_pct

                return {
                    "trend": trend,
                    "change_pct": round(change_pct, 2),
                    "reference_value": recent_value,
                    "price_low": week_ago_value,
                    "current_value": current_market_value,
                    "has_data": True,
                }

        # Fallback: Use reference price but be MORE CONSERVATIVE
        reference_price = history_data.get("trp", 0)
        peak_value = history_data.get("hmv", 0)

        # CRITICAL: If current is below peak, check if falling from peak
        if peak_value > 0 and current_market_value < peak_value * 0.95:
            # More than 5% below peak = likely falling
            decline_from_peak = ((current_market_value - peak_value) / peak_value) * 100
            return {
                "trend": "falling",  # Assume falling if off peak
                "change_pct": round(decline_from_peak, 2),
                "reference_value": peak_value,
                "current_value": current_market_value,
                "has_data": True,
            }

        # If no historical data, use reference but be conservative
        if reference_price > 0:
            change_pct = ((current_market_value - reference_price) / reference_price) * 100
        else:
            return {"trend": "unknown", "change_pct": 0.0, "has_data": False}

        # Much stricter thresholds when using old reference data
        if change_pct > 20:  # Was 10
            trend = "rising"
        elif change_pct < -5:  # Was -10
            trend = "falling"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "change_pct": round(change_pct, 2),
            "reference_value": reference_price,
            "current_value": current_market_value,
            "has_data": True,
        }

    def get_cached_performance(
        self,
        player_id: str,
        league_id: str,
        max_age_hours: int = 6,  # Performance changes less frequently
    ) -> dict[str, Any] | None:
        """Get cached player performance data"""
        cutoff = int((datetime.now() - timedelta(hours=max_age_hours)).timestamp())

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT data, fetched_at
                FROM performance_cache
                WHERE player_id = ? AND league_id = ? AND fetched_at >= ?
            """,
                (player_id, league_id, cutoff),
            )

            row = cursor.fetchone()
            if row:
                return json.loads(row[0])

        return None

    def cache_performance(self, player_id: str, league_id: str, data: dict[str, Any]):
        """Cache player performance data"""
        timestamp = int(datetime.now().timestamp())

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO performance_cache
                (player_id, league_id, fetched_at, data)
                VALUES (?, ?, ?, ?)
            """,
                (player_id, league_id, timestamp, json.dumps(data)),
            )
            conn.commit()

    def cleanup_old_cache(self, days_to_keep: int = 7):
        """Remove cached data older than specified days"""
        cutoff = int((datetime.now() - timedelta(days=days_to_keep)).timestamp())

        with sqlite3.connect(self.db_path) as conn:
            result1 = conn.execute("DELETE FROM market_value_cache WHERE fetched_at < ?", (cutoff,))
            result2 = conn.execute("DELETE FROM performance_cache WHERE fetched_at < ?", (cutoff,))
            conn.commit()
            return result1.rowcount + result2.rowcount
