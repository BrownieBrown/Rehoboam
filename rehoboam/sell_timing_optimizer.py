"""Learn optimal selling times and strategies"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()


@dataclass
class HoldingPeriodRecommendation:
    """Recommendation for how long to hold a player"""

    optimal_hold_days: int
    confidence: float
    expected_profit_pct: float
    peak_probability: float  # Probability value has peaked

    # Context
    position: str
    trend: str
    reasoning: str


@dataclass
class SellSignalStrength:
    """Strength of various sell signals"""

    profit_target_signal: float  # 0-1
    peak_decline_signal: float
    schedule_difficulty_signal: float
    trend_reversal_signal: float

    combined_strength: float
    recommendation: str  # "SELL_NOW", "HOLD", "WAIT_FOR_PEAK"


class SellTimingOptimizer:
    """Learn optimal holding periods and sell timing from historical flips"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path("logs") / "bid_learning.db"

        self.db_path = db_path
        self._init_db()

        # Learning parameters
        self.min_hold_samples = 10
        self.target_profit_pct = 15.0  # Default target

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            # Table for tracking buy → sell lifecycle
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sell_timing_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    position TEXT,

                    -- Buy context
                    buy_price INTEGER NOT NULL,
                    buy_value_score REAL,
                    buy_trend TEXT,
                    buy_timestamp TEXT NOT NULL,

                    -- Hold period analysis (sampled every 7 days)
                    day_7_value INTEGER,
                    day_7_profit_pct REAL,
                    day_14_value INTEGER,
                    day_14_profit_pct REAL,
                    day_30_value INTEGER,
                    day_30_profit_pct REAL,

                    -- Actual sell (if executed)
                    actual_sell_day INTEGER,
                    actual_sell_price INTEGER,
                    actual_profit_pct REAL,

                    -- Peak detection
                    peak_value INTEGER,
                    peak_day INTEGER,
                    missed_peak_profit_pct REAL,

                    -- Context at sell
                    trend_at_sell TEXT,
                    injury_during_hold INTEGER DEFAULT 0,

                    timestamp TEXT NOT NULL
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sell_timing_position
                ON sell_timing_outcomes(position)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sell_timing_timestamp
                ON sell_timing_outcomes(timestamp)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sell_timing_player
                ON sell_timing_outcomes(player_id)
            """
            )

    def record_buy_for_tracking(
        self,
        player_id: str,
        player_name: str,
        position: str,
        buy_price: int,
        value_score: float,
        trend: str | None,
    ):
        """
        Record a buy transaction for hold period tracking.

        Args:
            player_id: Player ID
            player_name: Player name
            position: Player position
            buy_price: Purchase price
            value_score: Value score at purchase
            trend: Trend at purchase time
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sell_timing_outcomes
                (player_id, player_name, position, buy_price, buy_value_score,
                 buy_trend, buy_timestamp, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    player_name,
                    position,
                    buy_price,
                    value_score,
                    trend,
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )

    def update_hold_period_snapshots(self, api_client, league_id: str):
        """
        Update value snapshots for players we're tracking.

        Called periodically (daily) to track value evolution.

        Args:
            api_client: KickbaseAPI instance
            league_id: League ID
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get active tracking records (not yet sold)
            cursor = conn.execute(
                """
                SELECT id, player_id, buy_price, buy_timestamp
                FROM sell_timing_outcomes
                WHERE actual_sell_day IS NULL
                """
            )
            tracking = cursor.fetchall()

        if not tracking:
            return

        # Fetch current values
        player_ids = [t[1] for t in tracking]
        current_values = self._fetch_current_values(api_client, league_id, player_ids)

        # Update snapshots
        updated = 0
        for rec_id, player_id, buy_price, buy_ts in tracking:
            try:
                buy_dt = datetime.fromisoformat(buy_ts)
                days_held = (datetime.now() - buy_dt).days

                current_value = current_values.get(player_id)
                if not current_value:
                    continue

                profit_pct = ((current_value - buy_price) / buy_price) * 100

                # Update appropriate day snapshot
                with sqlite3.connect(self.db_path) as conn:
                    if 6 <= days_held <= 8:  # Day 7 snapshot
                        conn.execute(
                            """
                            UPDATE sell_timing_outcomes
                            SET day_7_value = ?, day_7_profit_pct = ?
                            WHERE id = ?
                            """,
                            (current_value, profit_pct, rec_id),
                        )
                        updated += 1
                    elif 13 <= days_held <= 15:  # Day 14 snapshot
                        conn.execute(
                            """
                            UPDATE sell_timing_outcomes
                            SET day_14_value = ?, day_14_profit_pct = ?
                            WHERE id = ?
                            """,
                            (current_value, profit_pct, rec_id),
                        )
                        updated += 1
                    elif 29 <= days_held <= 31:  # Day 30 snapshot
                        conn.execute(
                            """
                            UPDATE sell_timing_outcomes
                            SET day_30_value = ?, day_30_profit_pct = ?
                            WHERE id = ?
                            """,
                            (current_value, profit_pct, rec_id),
                        )
                        updated += 1

                    # Always update peak if this is highest value seen
                    cursor = conn.execute(
                        "SELECT peak_value FROM sell_timing_outcomes WHERE id = ?", (rec_id,)
                    )
                    peak = cursor.fetchone()[0]

                    if not peak or current_value > peak:
                        conn.execute(
                            """
                            UPDATE sell_timing_outcomes
                            SET peak_value = ?, peak_day = ?
                            WHERE id = ?
                            """,
                            (current_value, days_held, rec_id),
                        )

            except Exception as e:
                console.print(
                    f"[dim]Warning: Could not update snapshot for player {player_id}: {e}[/dim]"
                )

        if updated > 0:
            console.print(f"[dim]✓ Updated {updated} sell timing snapshots[/dim]")

    def _fetch_current_values(
        self, api_client, league_id: str, player_ids: list[str]
    ) -> dict[str, int]:
        """Fetch current market values for players"""
        values = {}

        try:
            from .kickbase_client import League

            league = League(id=league_id, name="", creator_id="")

            # Check market
            market_players = api_client.get_market(league)
            for player in market_players:
                if player.id in player_ids:
                    values[player.id] = player.market_value

            # Check squad
            squad = api_client.get_squad(league)
            for player in squad:
                if player.id in player_ids:
                    values[player.id] = player.market_value

        except Exception as e:
            console.print(f"[yellow]Warning: Could not fetch values: {e}[/yellow]")

        return values

    def get_optimal_hold_period(
        self, position: str, trend: str | None, value_score: float
    ) -> HoldingPeriodRecommendation:
        """
        Get recommended holding period based on historical patterns.

        Args:
            position: Player position
            trend: Current trend
            value_score: Quality score

        Returns:
            HoldingPeriodRecommendation with optimal days and expected profit
        """
        # Map full position names to short codes
        position_map = {
            "Goalkeeper": "GK",
            "Defender": "DEF",
            "Midfielder": "MID",
            "Forward": "FWD",
        }
        position_code = position_map.get(position, position)

        with sqlite3.connect(self.db_path) as conn:
            # Analyze historical hold periods for similar players
            cursor = conn.execute(
                """
                SELECT
                    AVG(actual_sell_day),
                    AVG(actual_profit_pct),
                    AVG(peak_day),
                    AVG((peak_value - buy_price) * 100.0 / buy_price),
                    COUNT(*)
                FROM sell_timing_outcomes
                WHERE position = ?
                  AND actual_sell_day IS NOT NULL
                """,
                (position_code,),
            )

            result = cursor.fetchone()

            if not result or result[4] < self.min_hold_samples:
                # Insufficient data - use defaults
                default_holds = {"FWD": 14, "MID": 21, "DEF": 21, "GK": 30}

                return HoldingPeriodRecommendation(
                    optimal_hold_days=default_holds.get(position_code, 21),
                    confidence=0.3,
                    expected_profit_pct=10.0,
                    peak_probability=0.5,
                    position=position_code,
                    trend=trend or "unknown",
                    reasoning="Insufficient historical data - using defaults",
                )

            avg_hold, avg_profit, avg_peak_day, avg_peak_profit, count = result

            # Adjust for trend
            if trend == "rising":
                # Rising trends peak later
                optimal_days = int(avg_peak_day * 1.2) if avg_peak_day else int(avg_hold * 1.2)
                expected_profit = avg_peak_profit if avg_peak_profit else avg_profit
                reasoning = f"Rising trend - hold for peak (~{optimal_days} days)"
            elif trend == "falling":
                # Falling trends - sell quickly
                optimal_days = max(7, int(avg_hold * 0.7)) if avg_hold else 7
                expected_profit = avg_profit if avg_profit else 5.0
                reasoning = f"Falling trend - quick flip ({optimal_days} days)"
            else:
                optimal_days = int(avg_hold) if avg_hold else 14
                expected_profit = avg_profit if avg_profit else 10.0
                reasoning = f"Historical average for {position_code}"

            confidence = min(1.0, count / 30.0)  # Full confidence at 30+ samples

            return HoldingPeriodRecommendation(
                optimal_hold_days=optimal_days,
                confidence=confidence,
                expected_profit_pct=expected_profit,
                peak_probability=0.7 if trend == "rising" else 0.5,
                position=position_code,
                trend=trend or "stable",
                reasoning=reasoning,
            )

    def evaluate_sell_signals(
        self,
        player_id: str,
        current_value: int,
        current_trend: str | None,
        sos_rating: str | None,
        days_held: int,
    ) -> SellSignalStrength:
        """
        Evaluate strength of sell signals for a player we own.

        Args:
            player_id: Player ID
            current_value: Current market value
            current_trend: Current trend direction
            sos_rating: Strength of schedule rating
            days_held: Days since purchase

        Returns:
            SellSignalStrength with signal analysis
        """
        # Get tracking record
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT buy_price, position, buy_trend, peak_value, peak_day
                FROM sell_timing_outcomes
                WHERE player_id = ?
                  AND actual_sell_day IS NULL
                ORDER BY buy_timestamp DESC
                LIMIT 1
                """,
                (player_id,),
            )
            result = cursor.fetchone()

        if not result:
            # Not tracking this player - use simple signals
            return SellSignalStrength(
                profit_target_signal=0.5,
                peak_decline_signal=0.0,
                schedule_difficulty_signal=0.0,
                trend_reversal_signal=0.0,
                combined_strength=0.5,
                recommendation="HOLD",
            )

        buy_price, position, buy_trend, peak_value, peak_day = result

        # Calculate signals
        profit_pct = ((current_value - buy_price) / buy_price) * 100

        # Signal 1: Profit target (0-1)
        profit_signal = min(1.0, max(0.0, profit_pct / self.target_profit_pct))

        # Signal 2: Peak decline (0-1)
        peak_signal = 0.0
        if peak_value and peak_value > current_value:
            decline_pct = ((peak_value - current_value) / peak_value) * 100
            peak_signal = min(1.0, decline_pct / 10.0)  # 10% decline = full signal

        # Signal 3: Schedule difficulty (0-1)
        schedule_signal = 0.0
        if sos_rating in ["Difficult", "Very Difficult"] and profit_pct > 5:
            schedule_signal = 0.7 if sos_rating == "Difficult" else 0.9

        # Signal 4: Trend reversal (0-1)
        trend_signal = 0.0
        if buy_trend == "rising" and current_trend == "falling":
            trend_signal = 0.8  # Strong reversal signal
        elif buy_trend == "stable" and current_trend == "falling":
            trend_signal = 0.5

        # Combined strength (weighted average)
        combined = (
            profit_signal * 0.4 + peak_signal * 0.3 + schedule_signal * 0.2 + trend_signal * 0.1
        )

        # Recommendation
        if combined >= 0.7:
            recommendation = "SELL_NOW"
        elif combined >= 0.5:
            recommendation = "HOLD"
        else:
            recommendation = "WAIT_FOR_PEAK"

        return SellSignalStrength(
            profit_target_signal=profit_signal,
            peak_decline_signal=peak_signal,
            schedule_difficulty_signal=schedule_signal,
            trend_reversal_signal=trend_signal,
            combined_strength=combined,
            recommendation=recommendation,
        )

    def analyze_missed_peaks(self) -> dict[str, Any]:
        """
        Analyze how much profit we left on the table by not selling at peak.

        Returns:
            Statistics on peak timing accuracy
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT
                    AVG(missed_peak_profit_pct),
                    AVG(peak_day - actual_sell_day),
                    COUNT(*)
                FROM sell_timing_outcomes
                WHERE actual_sell_day IS NOT NULL
                  AND peak_day IS NOT NULL
                  AND missed_peak_profit_pct IS NOT NULL
                """
            )

            result = cursor.fetchone()

            if not result or result[2] == 0:
                return {
                    "avg_missed_profit_pct": 0.0,
                    "avg_timing_error_days": 0.0,
                    "sample_size": 0,
                    "message": "Not enough data",
                }

            avg_missed, avg_timing_error, count = result

            timing_direction = "before" if avg_timing_error < 0 else "after"
            abs_timing_error = abs(avg_timing_error) if avg_timing_error else 0

            return {
                "avg_missed_profit_pct": round(avg_missed, 1) if avg_missed else 0.0,
                "avg_timing_error_days": round(avg_timing_error, 1) if avg_timing_error else 0.0,
                "sample_size": count,
                "message": f"On average, selling {abs_timing_error:.0f} days {timing_direction} peak",
            }
