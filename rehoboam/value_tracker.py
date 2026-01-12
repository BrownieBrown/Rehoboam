"""Track player values over time to detect peaks and selling opportunities"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class ValueSnapshot:
    """Snapshot of player value at a point in time"""

    player_id: str
    player_name: str
    market_value: int
    points: int
    average_points: float
    timestamp: float
    league_id: str


@dataclass
class PeakAnalysis:
    """Analysis of player's value peak"""

    player_id: str
    player_name: str
    current_value: int
    peak_value: int
    peak_timestamp: float
    decline_from_peak_pct: float
    decline_from_peak_amount: int
    days_since_peak: int
    is_declining: bool


class ValueTracker:
    """Track player values over time and detect peaks"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path("logs") / "value_tracking.db"

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            # Value snapshots over time
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS value_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    league_id TEXT NOT NULL,
                    market_value INTEGER NOT NULL,
                    points INTEGER NOT NULL,
                    average_points REAL NOT NULL,
                    timestamp REAL NOT NULL
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_player_timestamp
                ON value_snapshots(player_id, timestamp DESC)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_league
                ON value_snapshots(league_id)
            """
            )

            # Purchase tracking (when we bought players)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    league_id TEXT NOT NULL,
                    purchase_price INTEGER NOT NULL,
                    purchase_timestamp REAL NOT NULL,
                    UNIQUE(player_id, league_id)
                )
            """
            )

            # Daily price snapshots for volatility analysis
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_prices (
                    player_id TEXT NOT NULL,
                    league_id TEXT NOT NULL,
                    market_value INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    PRIMARY KEY (player_id, league_id, date)
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_daily_prices_date
                ON daily_prices(date)
            """
            )

            # Risk metrics cache
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS risk_metrics_cache (
                    player_id TEXT PRIMARY KEY,
                    price_volatility REAL,
                    performance_volatility REAL,
                    var_7d REAL,
                    var_30d REAL,
                    sharpe_ratio REAL,
                    calculated_at TEXT,
                    data_quality TEXT
                )
            """
            )

            conn.commit()

    def record_snapshot(self, snapshot: ValueSnapshot):
        """Record a value snapshot for a player"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO value_snapshots (
                    player_id, player_name, league_id, market_value,
                    points, average_points, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    snapshot.player_id,
                    snapshot.player_name,
                    snapshot.league_id,
                    snapshot.market_value,
                    snapshot.points,
                    snapshot.average_points,
                    snapshot.timestamp,
                ),
            )
            conn.commit()

    def record_snapshots_bulk(self, snapshots: list[ValueSnapshot]):
        """Record multiple snapshots at once (for squad tracking)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO value_snapshots (
                    player_id, player_name, league_id, market_value,
                    points, average_points, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    (
                        s.player_id,
                        s.player_name,
                        s.league_id,
                        s.market_value,
                        s.points,
                        s.average_points,
                        s.timestamp,
                    )
                    for s in snapshots
                ],
            )
            conn.commit()

    def record_purchase(
        self,
        player_id: str,
        player_name: str,
        league_id: str,
        purchase_price: int,
        timestamp: float | None = None,
    ):
        """Record when we purchased a player"""
        if timestamp is None:
            timestamp = datetime.now().timestamp()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO purchases (
                    player_id, player_name, league_id, purchase_price, purchase_timestamp
                )
                VALUES (?, ?, ?, ?, ?)
            """,
                (player_id, player_name, league_id, purchase_price, timestamp),
            )
            conn.commit()

    def get_purchase_info(self, player_id: str, league_id: str) -> dict[str, Any] | None:
        """Get purchase information for a player"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT purchase_price, purchase_timestamp
                FROM purchases
                WHERE player_id = ? AND league_id = ?
            """,
                (player_id, league_id),
            )

            result = cursor.fetchone()
            if not result:
                return None

            return {
                "purchase_price": result[0],
                "purchase_timestamp": result[1],
                "days_owned": int((datetime.now().timestamp() - result[1]) / (24 * 3600)),
            }

    def get_peak_analysis(
        self, player_id: str, league_id: str, current_value: int
    ) -> PeakAnalysis | None:
        """
        Analyze if player has peaked and is declining

        Args:
            player_id: Player ID
            league_id: League ID
            current_value: Current market value

        Returns:
            PeakAnalysis if we have historical data, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get historical values
            cursor = conn.execute(
                """
                SELECT player_name, market_value, timestamp
                FROM value_snapshots
                WHERE player_id = ? AND league_id = ?
                ORDER BY timestamp DESC
                LIMIT 100
            """,
                (player_id, league_id),
            )

            snapshots = cursor.fetchall()

            if not snapshots or len(snapshots) < 3:
                # Need at least 3 data points to detect peak
                return None

            player_name = snapshots[0][0]

            # Find peak value
            peak_value = max(s[1] for s in snapshots)
            peak_snapshot = next(s for s in snapshots if s[1] == peak_value)
            peak_timestamp = peak_snapshot[2]

            # Calculate decline from peak
            if peak_value > 0:
                decline_pct = ((current_value - peak_value) / peak_value) * 100
            else:
                decline_pct = 0.0

            decline_amount = current_value - peak_value

            days_since_peak = int((datetime.now().timestamp() - peak_timestamp) / (24 * 3600))

            # Determine if declining (peak was at least 7 days ago and value dropped)
            is_declining = days_since_peak >= 7 and decline_pct < -5.0

            return PeakAnalysis(
                player_id=player_id,
                player_name=player_name,
                current_value=current_value,
                peak_value=peak_value,
                peak_timestamp=peak_timestamp,
                decline_from_peak_pct=decline_pct,
                decline_from_peak_amount=decline_amount,
                days_since_peak=days_since_peak,
                is_declining=is_declining,
            )

    def get_value_trend(self, player_id: str, league_id: str, days: int = 14) -> dict[str, Any]:
        """Get value trend over specified days"""
        with sqlite3.connect(self.db_path) as conn:
            cutoff = datetime.now().timestamp() - (days * 24 * 3600)

            cursor = conn.execute(
                """
                SELECT market_value, timestamp
                FROM value_snapshots
                WHERE player_id = ? AND league_id = ? AND timestamp > ?
                ORDER BY timestamp ASC
            """,
                (player_id, league_id, cutoff),
            )

            values = cursor.fetchall()

            if len(values) < 2:
                return {"has_data": False, "trend": "unknown", "change_pct": 0.0}

            oldest_value = values[0][0]
            newest_value = values[-1][0]

            if oldest_value > 0:
                change_pct = ((newest_value - oldest_value) / oldest_value) * 100
            else:
                change_pct = 0.0

            # Determine trend
            if change_pct > 10:
                trend = "strongly rising"
            elif change_pct > 5:
                trend = "rising"
            elif change_pct > -5:
                trend = "stable"
            elif change_pct > -10:
                trend = "falling"
            else:
                trend = "strongly falling"

            return {
                "has_data": True,
                "trend": trend,
                "change_pct": change_pct,
                "oldest_value": oldest_value,
                "newest_value": newest_value,
                "days_tracked": days,
                "snapshots": len(values),
            }

    def get_statistics(self, league_id: str) -> dict[str, Any]:
        """Get overall tracking statistics"""
        with sqlite3.connect(self.db_path) as conn:
            # Total snapshots
            cursor = conn.execute(
                """
                SELECT COUNT(DISTINCT player_id), COUNT(*)
                FROM value_snapshots
                WHERE league_id = ?
            """,
                (league_id,),
            )
            unique_players, total_snapshots = cursor.fetchone()

            # Players we own
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM purchases
                WHERE league_id = ?
            """,
                (league_id,),
            )
            owned_players = cursor.fetchone()[0]

            # Oldest snapshot
            cursor = conn.execute(
                """
                SELECT MIN(timestamp)
                FROM value_snapshots
                WHERE league_id = ?
            """,
                (league_id,),
            )
            oldest_timestamp = cursor.fetchone()[0]

            if oldest_timestamp:
                days_tracking = int((datetime.now().timestamp() - oldest_timestamp) / (24 * 3600))
            else:
                days_tracking = 0

            return {
                "unique_players_tracked": unique_players,
                "total_snapshots": total_snapshots,
                "owned_players": owned_players,
                "days_tracking": days_tracking,
            }

    def record_daily_price(
        self, player_id: str, league_id: str, market_value: int, date: str | None = None
    ):
        """Record daily price snapshot for volatility analysis"""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_prices (player_id, league_id, market_value, date)
                VALUES (?, ?, ?, ?)
            """,
                (player_id, league_id, market_value, date),
            )
            conn.commit()

    def get_daily_prices(self, player_id: str, league_id: str, days: int = 30) -> list[int]:
        """
        Get daily price history for a player

        Args:
            player_id: Player ID
            league_id: League ID
            days: Number of days to look back

        Returns:
            List of daily prices (most recent first)
        """
        with sqlite3.connect(self.db_path) as conn:
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            cursor = conn.execute(
                """
                SELECT market_value
                FROM daily_prices
                WHERE player_id = ? AND league_id = ? AND date >= ?
                ORDER BY date DESC
            """,
                (player_id, league_id, cutoff_date),
            )

            return [row[0] for row in cursor.fetchall()]

    def cache_risk_metrics(
        self,
        player_id: str,
        price_volatility: float,
        performance_volatility: float,
        var_7d: float,
        var_30d: float,
        sharpe_ratio: float,
        data_quality: str,
    ):
        """Cache calculated risk metrics"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO risk_metrics_cache (
                    player_id, price_volatility, performance_volatility,
                    var_7d, var_30d, sharpe_ratio, calculated_at, data_quality
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    player_id,
                    price_volatility,
                    performance_volatility,
                    var_7d,
                    var_30d,
                    sharpe_ratio,
                    datetime.now().isoformat(),
                    data_quality,
                ),
            )
            conn.commit()

    def get_cached_risk_metrics(
        self, player_id: str, max_age_hours: int = 6
    ) -> dict[str, Any] | None:
        """
        Get cached risk metrics if recent enough

        Args:
            player_id: Player ID
            max_age_hours: Maximum age of cache in hours

        Returns:
            Cached metrics or None if too old/not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT price_volatility, performance_volatility, var_7d, var_30d,
                       sharpe_ratio, calculated_at, data_quality
                FROM risk_metrics_cache
                WHERE player_id = ?
            """,
                (player_id,),
            )

            result = cursor.fetchone()
            if not result:
                return None

            # Check if cache is fresh enough
            calculated_at = datetime.fromisoformat(result[5])
            age_hours = (datetime.now() - calculated_at).total_seconds() / 3600

            if age_hours > max_age_hours:
                return None

            return {
                "price_volatility": result[0],
                "performance_volatility": result[1],
                "var_7d": result[2],
                "var_30d": result[3],
                "sharpe_ratio": result[4],
                "calculated_at": result[5],
                "data_quality": result[6],
            }
