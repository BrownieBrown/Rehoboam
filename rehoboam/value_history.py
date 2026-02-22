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
        """Analyze trend from API history data.

        DEPRECATED: Use TrendService.analyze() instead. This method delegates
        to TrendService for backwards compatibility with any remaining callers.
        """
        from .services.trend_service import TrendService

        result = TrendService.analyze(history_data, current_market_value)
        return result.to_dict()

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
