"""Learn from auction outcomes to improve bidding strategy"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class AuctionOutcome:
    """Record of an auction result"""

    player_id: str
    player_name: str
    our_bid: int
    asking_price: int
    our_overbid_pct: float
    won: bool
    winning_bid: int | None = None
    winning_overbid_pct: float | None = None
    winner_user_id: str | None = None
    timestamp: float = None
    player_value_score: float | None = None
    market_value: int | None = None


@dataclass
class FlipOutcome:
    """Record of a completed flip (buy + sell)"""

    player_id: str
    player_name: str
    buy_price: int
    sell_price: int
    profit: int
    profit_pct: float
    hold_days: int
    buy_date: float
    sell_date: float
    trend_at_buy: str | None = None  # rising, falling, stable
    average_points: float | None = None
    position: str | None = None
    was_injured: bool = False


class BidLearner:
    """Learn from auction outcomes to improve bidding strategy"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path("logs") / "bid_learning.db"

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auction_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    our_bid INTEGER NOT NULL,
                    asking_price INTEGER NOT NULL,
                    our_overbid_pct REAL NOT NULL,
                    won INTEGER NOT NULL,
                    winning_bid INTEGER,
                    winning_overbid_pct REAL,
                    winner_user_id TEXT,
                    timestamp REAL NOT NULL,
                    player_value_score REAL,
                    market_value INTEGER
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_player_id
                ON auction_outcomes(player_id)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON auction_outcomes(timestamp)
            """
            )

            # Flip outcomes table for tracking buy+sell transactions
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flip_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    buy_price INTEGER NOT NULL,
                    sell_price INTEGER NOT NULL,
                    profit INTEGER NOT NULL,
                    profit_pct REAL NOT NULL,
                    hold_days INTEGER NOT NULL,
                    buy_date REAL NOT NULL,
                    sell_date REAL NOT NULL,
                    trend_at_buy TEXT,
                    average_points REAL,
                    position TEXT,
                    was_injured INTEGER NOT NULL DEFAULT 0
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flip_player_id
                ON flip_outcomes(player_id)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flip_buy_date
                ON flip_outcomes(buy_date)
            """
            )

            conn.commit()

    def record_outcome(self, outcome: AuctionOutcome):
        """Record an auction outcome for learning"""
        if outcome.timestamp is None:
            outcome.timestamp = datetime.now().timestamp()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO auction_outcomes (
                    player_id, player_name, our_bid, asking_price, our_overbid_pct,
                    won, winning_bid, winning_overbid_pct, winner_user_id, timestamp,
                    player_value_score, market_value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    outcome.player_id,
                    outcome.player_name,
                    outcome.our_bid,
                    outcome.asking_price,
                    outcome.our_overbid_pct,
                    1 if outcome.won else 0,
                    outcome.winning_bid,
                    outcome.winning_overbid_pct,
                    outcome.winner_user_id,
                    outcome.timestamp,
                    outcome.player_value_score,
                    outcome.market_value,
                ),
            )
            conn.commit()

    def record_flip(self, outcome: FlipOutcome):
        """Record a completed flip for learning"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO flip_outcomes (
                    player_id, player_name, buy_price, sell_price, profit, profit_pct,
                    hold_days, buy_date, sell_date, trend_at_buy, average_points, position,
                    was_injured
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    outcome.player_id,
                    outcome.player_name,
                    outcome.buy_price,
                    outcome.sell_price,
                    outcome.profit,
                    outcome.profit_pct,
                    outcome.hold_days,
                    outcome.buy_date,
                    outcome.sell_date,
                    outcome.trend_at_buy,
                    outcome.average_points,
                    outcome.position,
                    1 if outcome.was_injured else 0,
                ),
            )
            conn.commit()

    def get_recommended_overbid(
        self,
        asking_price: int,
        value_score: float,
        market_value: int,
        predicted_future_value: int | None = None,
    ) -> dict[str, Any]:
        """
        Get recommended overbid based on learning from past auctions.
        NEVER recommends bidding above predicted future value.

        Args:
            asking_price: Player's asking price
            value_score: Our calculated value score (0-100)
            market_value: Player's market value
            predicted_future_value: Maximum we should pay (value ceiling)

        Returns:
            dict with recommended_overbid_pct, confidence, reason, max_bid
        """
        # Calculate value ceiling if not provided
        if predicted_future_value is None:
            # Estimate based on value score and market value
            # High value score = expect 10-20% growth
            growth_factor = 1.0 + (value_score / 1000)  # 60 score = 6% growth
            predicted_future_value = int(market_value * growth_factor)

        # Calculate absolute maximum overbid allowed by value ceiling
        if predicted_future_value <= asking_price:
            # Predicted value is below asking price - don't bid
            return {
                "recommended_overbid_pct": 0.0,
                "confidence": "high",
                "reason": f"Predicted value (€{predicted_future_value:,}) below asking price - SKIP",
                "based_on_auctions": 0,
                "max_bid": predicted_future_value,
            }

        max_overbid_amount = predicted_future_value - asking_price
        max_overbid_pct = (max_overbid_amount / asking_price) * 100
        with sqlite3.connect(self.db_path) as conn:
            # Get recent auction outcomes
            cursor = conn.execute(
                """
                SELECT our_overbid_pct, won, winning_overbid_pct
                FROM auction_outcomes
                WHERE timestamp > ?
                ORDER BY timestamp DESC
                LIMIT 100
            """,
                (datetime.now().timestamp() - (30 * 24 * 3600),),
            )  # Last 30 days

            outcomes = cursor.fetchall()

            if not outcomes or len(outcomes) < 5:
                # Conservative default: 8%, but never exceed value ceiling
                default_overbid = min(8.0, max_overbid_pct)
                return {
                    "recommended_overbid_pct": default_overbid,
                    "confidence": "low",
                    "reason": f"Insufficient data - using conservative {default_overbid:.1f}% (max: {max_overbid_pct:.1f}%)",
                    "based_on_auctions": len(outcomes),
                    "max_bid": predicted_future_value,
                    "value_ceiling_applied": default_overbid < max_overbid_pct,
                }

            # Analyze patterns
            wins = [o for o in outcomes if o[1] == 1]
            losses = [o for o in outcomes if o[1] == 0]

            if not wins:
                # We never won - need to bid higher, but respect ceiling
                if losses:
                    avg_losing_overbid = sum(o[0] for o in losses) / len(losses)
                    recommended = min(avg_losing_overbid + 5.0, max_overbid_pct)
                    ceiling_applied = (avg_losing_overbid + 5.0) > max_overbid_pct
                    return {
                        "recommended_overbid_pct": recommended,
                        "confidence": "medium",
                        "reason": f"Lost all {len(losses)} recent auctions - bidding higher (max: {max_overbid_pct:.1f}%)",
                        "based_on_auctions": len(outcomes),
                        "max_bid": predicted_future_value,
                        "value_ceiling_applied": ceiling_applied,
                    }
                else:
                    recommended = min(10.0, max_overbid_pct)
                    return {
                        "recommended_overbid_pct": recommended,
                        "confidence": "low",
                        "reason": f"No wins yet - trying {recommended:.1f}% overbid (max: {max_overbid_pct:.1f}%)",
                        "based_on_auctions": len(outcomes),
                        "max_bid": predicted_future_value,
                        "value_ceiling_applied": recommended < 10.0,
                    }

            # Calculate winning overbid stats
            winning_overbids = [o[0] for o in wins]
            avg_winning_overbid = sum(winning_overbids) / len(winning_overbids)

            # If we have data about what others bid
            competitor_overbids = [o[2] for o in losses if o[2] is not None]

            if competitor_overbids:
                # We know what beats us
                avg_competitor_overbid = sum(competitor_overbids) / len(competitor_overbids)
                # Recommend slightly above average competitor
                recommended = avg_competitor_overbid + 2.0
            else:
                # Use our own winning pattern
                recommended = avg_winning_overbid

            # Adjust for value score (before applying ceiling)
            if value_score >= 80:
                # High value - bid aggressively
                recommended = max(recommended, 12.0)
            elif value_score < 50:
                # Low value - bid conservatively
                recommended = min(recommended, 8.0)

            # Cap at reasonable operational limits
            recommended = max(5.0, min(recommended, 20.0))

            # CRITICAL: Apply value ceiling (never exceed predicted value)
            original_recommended = recommended
            recommended = min(recommended, max_overbid_pct)
            ceiling_applied = original_recommended > recommended

            win_rate = len(wins) / len(outcomes) * 100

            reason = f"Based on {len(outcomes)} auctions ({win_rate:.0f}% win rate)"
            if ceiling_applied:
                reason += f" | Value ceiling applied (max: {max_overbid_pct:.1f}%)"

            return {
                "recommended_overbid_pct": round(recommended, 1),
                "confidence": "high" if len(outcomes) >= 20 else "medium",
                "reason": reason,
                "based_on_auctions": len(outcomes),
                "win_rate": round(win_rate, 1),
                "avg_winning_overbid": round(avg_winning_overbid, 1) if wins else None,
                "avg_competitor_overbid": (
                    round(avg_competitor_overbid, 1) if competitor_overbids else None
                ),
                "max_bid": predicted_future_value,
                "value_ceiling_applied": ceiling_applied,
            }

    def get_statistics(self) -> dict[str, Any]:
        """Get overall learning statistics"""
        with sqlite3.connect(self.db_path) as conn:
            # Total auctions
            cursor = conn.execute("SELECT COUNT(*) FROM auction_outcomes")
            total = cursor.fetchone()[0]

            if total == 0:
                return {"total_auctions": 0, "wins": 0, "losses": 0, "win_rate": 0.0}

            # Wins/losses
            cursor = conn.execute("SELECT COUNT(*) FROM auction_outcomes WHERE won = 1")
            wins = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM auction_outcomes WHERE won = 0")
            losses = cursor.fetchone()[0]

            # Average overbid
            cursor = conn.execute("SELECT AVG(our_overbid_pct) FROM auction_outcomes WHERE won = 1")
            avg_winning_overbid = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT AVG(our_overbid_pct) FROM auction_outcomes WHERE won = 0")
            avg_losing_overbid = cursor.fetchone()[0] or 0

            # Value score correlation
            cursor = conn.execute(
                """
                SELECT AVG(player_value_score) FROM auction_outcomes
                WHERE won = 1 AND player_value_score IS NOT NULL
            """
            )
            avg_value_score_wins = cursor.fetchone()[0] or 0

            cursor = conn.execute(
                """
                SELECT AVG(player_value_score) FROM auction_outcomes
                WHERE won = 0 AND player_value_score IS NOT NULL
            """
            )
            avg_value_score_losses = cursor.fetchone()[0] or 0

            return {
                "total_auctions": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round((wins / total * 100) if total > 0 else 0, 1),
                "avg_winning_overbid": round(avg_winning_overbid, 1),
                "avg_losing_overbid": round(avg_losing_overbid, 1),
                "avg_value_score_wins": round(avg_value_score_wins, 1),
                "avg_value_score_losses": round(avg_value_score_losses, 1),
            }

    def analyze_competitor(self, user_id: str) -> dict[str, Any]:
        """Analyze a specific competitor's bidding patterns"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*), AVG(winning_overbid_pct), MIN(winning_overbid_pct), MAX(winning_overbid_pct)
                FROM auction_outcomes
                WHERE winner_user_id = ? AND winning_overbid_pct IS NOT NULL
            """,
                (user_id,),
            )

            result = cursor.fetchone()
            count, avg_overbid, min_overbid, max_overbid = result

            if count == 0 or count is None:
                return {
                    "user_id": user_id,
                    "times_beaten_us": 0,
                    "message": "No data on this competitor",
                }

            return {
                "user_id": user_id,
                "times_beaten_us": count,
                "avg_overbid": round(avg_overbid, 1) if avg_overbid else None,
                "min_overbid": round(min_overbid, 1) if min_overbid else None,
                "max_overbid": round(max_overbid, 1) if max_overbid else None,
                "message": (
                    f"This user typically overbids {avg_overbid:.1f}%"
                    if avg_overbid
                    else "Unknown pattern"
                ),
            }

    def track_outcome_validation(self, player_id: str, current_market_value: int) -> dict[str, Any]:
        """
        Track whether past auction outcomes were good predictions

        Args:
            player_id: Player to check
            current_market_value: Current market value of player

        Returns:
            dict with validation info (was winner right to pay that much?)
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get our lost auctions for this player
            cursor = conn.execute(
                """
                SELECT our_bid, winning_bid, timestamp, asking_price, market_value
                FROM auction_outcomes
                WHERE player_id = ? AND won = 0 AND winning_bid IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 1
            """,
                (player_id,),
            )

            result = cursor.fetchone()

            if not result:
                return {
                    "player_id": player_id,
                    "has_data": False,
                    "message": "No past auction data for this player",
                }

            our_bid, winning_bid, timestamp, asking_price, old_market_value = result

            # Calculate value change since auction
            if old_market_value and old_market_value > 0:
                value_change_pct = (
                    (current_market_value - old_market_value) / old_market_value
                ) * 100
            else:
                value_change_pct = 0.0

            # Did winner overpay or get a good deal?
            winner_profit_pct = (
                ((current_market_value - winning_bid) / winning_bid) * 100 if winning_bid > 0 else 0
            )

            # We would have paid vs now
            our_profit_pct = (
                ((current_market_value - our_bid) / our_bid) * 100 if our_bid > 0 else 0
            )

            # Determine who was right
            if current_market_value >= winning_bid:
                assessment = "Winner got a good deal - our prediction was too conservative"
            elif current_market_value >= our_bid:
                assessment = "Both bids would have been profitable - we should have bid more"
            else:
                assessment = "Winner overpaid - we were right to skip"

            return {
                "player_id": player_id,
                "has_data": True,
                "our_bid": our_bid,
                "winning_bid": winning_bid,
                "auction_market_value": old_market_value,
                "current_market_value": current_market_value,
                "value_change_pct": round(value_change_pct, 1),
                "winner_profit_pct": round(winner_profit_pct, 1),
                "our_potential_profit_pct": round(our_profit_pct, 1),
                "assessment": assessment,
                "days_since_auction": int((datetime.now().timestamp() - timestamp) / (24 * 3600)),
            }

    def get_flip_statistics(self) -> dict[str, Any]:
        """Get statistics about completed flips for learning"""
        with sqlite3.connect(self.db_path) as conn:
            # Total flips
            cursor = conn.execute("SELECT COUNT(*) FROM flip_outcomes")
            total = cursor.fetchone()[0]

            if total == 0:
                return {
                    "total_flips": 0,
                    "profitable_flips": 0,
                    "unprofitable_flips": 0,
                    "success_rate": 0.0,
                }

            # Profitable vs unprofitable
            cursor = conn.execute("SELECT COUNT(*) FROM flip_outcomes WHERE profit > 0")
            profitable = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM flip_outcomes WHERE profit <= 0")
            unprofitable = cursor.fetchone()[0]

            # Average profit
            cursor = conn.execute("SELECT AVG(profit_pct) FROM flip_outcomes WHERE profit > 0")
            avg_profit_pct = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT AVG(profit_pct) FROM flip_outcomes WHERE profit <= 0")
            avg_loss_pct = cursor.fetchone()[0] or 0

            # Average hold time
            cursor = conn.execute("SELECT AVG(hold_days) FROM flip_outcomes WHERE profit > 0")
            avg_hold_days_profit = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT AVG(hold_days) FROM flip_outcomes WHERE profit <= 0")
            avg_hold_days_loss = cursor.fetchone()[0] or 0

            # Best flip
            cursor = conn.execute(
                """
                SELECT player_name, profit, profit_pct, hold_days
                FROM flip_outcomes
                ORDER BY profit_pct DESC
                LIMIT 1
            """
            )
            best_flip = cursor.fetchone()

            # Worst flip
            cursor = conn.execute(
                """
                SELECT player_name, profit, profit_pct, hold_days
                FROM flip_outcomes
                ORDER BY profit_pct ASC
                LIMIT 1
            """
            )
            worst_flip = cursor.fetchone()

            # Total profit
            cursor = conn.execute("SELECT SUM(profit) FROM flip_outcomes")
            total_profit = cursor.fetchone()[0] or 0

            return {
                "total_flips": total,
                "profitable_flips": profitable,
                "unprofitable_flips": unprofitable,
                "success_rate": round((profitable / total * 100) if total > 0 else 0, 1),
                "avg_profit_pct": round(avg_profit_pct, 1),
                "avg_loss_pct": round(avg_loss_pct, 1),
                "avg_hold_days_profit": round(avg_hold_days_profit, 1),
                "avg_hold_days_loss": round(avg_hold_days_loss, 1),
                "total_profit": total_profit,
                "best_flip": (
                    {
                        "player": best_flip[0],
                        "profit": best_flip[1],
                        "profit_pct": round(best_flip[2], 1),
                        "hold_days": best_flip[3],
                    }
                    if best_flip
                    else None
                ),
                "worst_flip": (
                    {
                        "player": worst_flip[0],
                        "profit": worst_flip[1],
                        "profit_pct": round(worst_flip[2], 1),
                        "hold_days": worst_flip[3],
                    }
                    if worst_flip
                    else None
                ),
            }

    def analyze_flip_patterns(self) -> dict[str, Any]:
        """Analyze patterns in flip outcomes to learn what works"""
        with sqlite3.connect(self.db_path) as conn:
            # Trend analysis
            cursor = conn.execute(
                """
                SELECT trend_at_buy, COUNT(*), AVG(profit_pct), SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)
                FROM flip_outcomes
                WHERE trend_at_buy IS NOT NULL
                GROUP BY trend_at_buy
            """
            )

            trend_results = {}
            for trend, count, avg_profit, wins in cursor.fetchall():
                trend_results[trend] = {
                    "count": count,
                    "avg_profit_pct": round(avg_profit, 1) if avg_profit else 0,
                    "success_rate": round((wins / count * 100) if count > 0 else 0, 1),
                }

            # Position analysis
            cursor = conn.execute(
                """
                SELECT position, COUNT(*), AVG(profit_pct), SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)
                FROM flip_outcomes
                WHERE position IS NOT NULL
                GROUP BY position
            """
            )

            position_results = {}
            for position, count, avg_profit, wins in cursor.fetchall():
                position_results[position] = {
                    "count": count,
                    "avg_profit_pct": round(avg_profit, 1) if avg_profit else 0,
                    "success_rate": round((wins / count * 100) if count > 0 else 0, 1),
                }

            # Hold time analysis (group by days)
            cursor = conn.execute(
                """
                SELECT
                    CASE
                        WHEN hold_days <= 1 THEN '0-1 days'
                        WHEN hold_days <= 3 THEN '2-3 days'
                        WHEN hold_days <= 7 THEN '4-7 days'
                        ELSE '8+ days'
                    END as hold_period,
                    COUNT(*),
                    AVG(profit_pct),
                    SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)
                FROM flip_outcomes
                GROUP BY hold_period
            """
            )

            hold_time_results = {}
            for period, count, avg_profit, wins in cursor.fetchall():
                hold_time_results[period] = {
                    "count": count,
                    "avg_profit_pct": round(avg_profit, 1) if avg_profit else 0,
                    "success_rate": round((wins / count * 100) if count > 0 else 0, 1),
                }

            # Injury impact
            cursor = conn.execute(
                """
                SELECT was_injured, COUNT(*), AVG(profit_pct), SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)
                FROM flip_outcomes
                GROUP BY was_injured
            """
            )

            injury_results = {}
            for was_injured, count, avg_profit, wins in cursor.fetchall():
                injury_results["injured" if was_injured else "healthy"] = {
                    "count": count,
                    "avg_profit_pct": round(avg_profit, 1) if avg_profit else 0,
                    "success_rate": round((wins / count * 100) if count > 0 else 0, 1),
                }

            return {
                "by_trend": trend_results,
                "by_position": position_results,
                "by_hold_time": hold_time_results,
                "by_injury_status": injury_results,
            }

    def get_learning_recommendations(self) -> list[str]:
        """Get actionable recommendations based on learning data"""
        recommendations = []

        patterns = self.analyze_flip_patterns()

        # Trend recommendations
        if "by_trend" in patterns and patterns["by_trend"]:
            trends = patterns["by_trend"]
            if "rising" in trends and "falling" in trends:
                rising_success = trends["rising"]["success_rate"]
                falling_success = trends["falling"]["success_rate"]

                if rising_success > falling_success + 20:
                    recommendations.append(
                        f"Focus on rising trend players (success rate: {rising_success}% vs {falling_success}% for falling)"
                    )
                elif falling_success > rising_success + 20:
                    recommendations.append(
                        f"Mean reversion strategy working well on falling players ({falling_success}% success rate)"
                    )

        # Position recommendations
        if "by_position" in patterns and patterns["by_position"]:
            positions = patterns["by_position"]
            sorted_positions = sorted(
                positions.items(), key=lambda x: x[1]["avg_profit_pct"], reverse=True
            )

            if sorted_positions:
                best_pos, best_stats = sorted_positions[0]
                if best_stats["count"] >= 3:  # Need at least 3 samples
                    recommendations.append(
                        f"Best position: {best_pos} ({best_stats['avg_profit_pct']}% avg profit, {best_stats['success_rate']}% success)"
                    )

        # Hold time recommendations
        if "by_hold_time" in patterns and patterns["by_hold_time"]:
            hold_times = patterns["by_hold_time"]
            sorted_holds = sorted(
                hold_times.items(), key=lambda x: x[1]["avg_profit_pct"], reverse=True
            )

            if sorted_holds:
                best_hold, best_stats = sorted_holds[0]
                if best_stats["count"] >= 3:
                    recommendations.append(
                        f"Optimal hold time: {best_hold} ({best_stats['avg_profit_pct']}% avg profit)"
                    )

        # Injury recommendations
        if "by_injury_status" in patterns and patterns["by_injury_status"]:
            injury = patterns["by_injury_status"]
            if "healthy" in injury and "injured" in injury:
                healthy_success = injury["healthy"]["success_rate"]
                injured_success = injury["injured"]["success_rate"]

                if healthy_success > injured_success + 15:
                    recommendations.append(
                        f"Avoid injured players (healthy success: {healthy_success}% vs injured: {injured_success}%)"
                    )

        if not recommendations:
            recommendations.append("Not enough data yet - keep trading to build learning database!")

        return recommendations
