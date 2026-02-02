"""Track recommendation history and learn from outcomes"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@dataclass
class RecommendationRecord:
    """A recommendation that was made"""

    id: int | None
    player_id: str
    player_name: str
    league_id: str

    # Recommendation details
    recommendation: str  # "BUY"|"SELL"|"HOLD"
    confidence: float
    value_score: float
    factors_json: str  # JSON array of ScoringFactor

    # Context at time of recommendation
    market_value: int
    trend_direction: str | None
    form_trajectory: str | None
    risk_category: str | None
    timestamp: str

    # Outcome tracking (filled in later)
    was_executed: bool = False
    execution_timestamp: str | None = None
    outcome_value: int | None = None  # Market value 7/14/30 days later
    outcome_points: int | None = None  # Points scored
    was_profitable: bool | None = None
    profit_amount: int | None = None
    outcome_timestamp: str | None = None


@dataclass
class FactorPerformance:
    """Performance metrics for a scoring factor"""

    factor_name: str
    recommendation_type: str  # "BUY"|"SELL"

    total_recommendations: int
    successful_outcomes: int
    win_rate: float
    avg_profit_pct: float
    avg_factor_score: float

    # Calibration
    confidence_70_accuracy: float | None
    confidence_80_accuracy: float | None
    confidence_90_accuracy: float | None

    last_updated: str


class HistoricalTracker:
    """Track recommendations and learn from outcomes"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path("logs") / "bid_learning.db"

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            # Recommendation history table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recommendation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    league_id TEXT NOT NULL,

                    recommendation TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    value_score REAL NOT NULL,
                    factors_json TEXT,

                    market_value INTEGER NOT NULL,
                    trend_direction TEXT,
                    form_trajectory TEXT,
                    risk_category TEXT,
                    timestamp TEXT NOT NULL,

                    was_executed INTEGER DEFAULT 0,
                    execution_timestamp TEXT,
                    outcome_value INTEGER,
                    outcome_points INTEGER,
                    was_profitable INTEGER,
                    profit_amount INTEGER,
                    outcome_timestamp TEXT
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rec_timestamp
                ON recommendation_history(timestamp)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rec_player
                ON recommendation_history(player_id)
            """
            )

            # Factor attribution table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS factor_attribution (
                    factor_name TEXT NOT NULL,
                    recommendation_type TEXT NOT NULL,

                    total_recommendations INTEGER DEFAULT 0,
                    successful_outcomes INTEGER DEFAULT 0,
                    win_rate REAL,
                    avg_profit_pct REAL,
                    avg_factor_score REAL,

                    confidence_70_accuracy REAL,
                    confidence_80_accuracy REAL,
                    confidence_90_accuracy REAL,

                    last_updated TEXT,
                    PRIMARY KEY (factor_name, recommendation_type)
                )
            """
            )

    def record_recommendation(
        self, player_analysis, league_id: str  # PlayerAnalysis object
    ) -> int:
        """
        Record a recommendation that was made

        Args:
            player_analysis: PlayerAnalysis object
            league_id: League ID

        Returns:
            ID of the inserted record
        """
        player = player_analysis.player
        player_name = f"{player.first_name} {player.last_name}"

        # Extract factors JSON
        factors_json = None
        if hasattr(player_analysis, "factors") and player_analysis.factors:
            factors_data = [
                {"name": f.name, "points": f.points, "description": f.description}
                for f in player_analysis.factors
            ]
            factors_json = json.dumps(factors_data)

        # Extract risk category
        risk_category = None
        if player_analysis.risk_metrics:
            risk_category = player_analysis.risk_metrics.risk_category

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO recommendation_history (
                    player_id, player_name, league_id,
                    recommendation, confidence, value_score, factors_json,
                    market_value, trend_direction, form_trajectory, risk_category,
                    timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    player.id,
                    player_name,
                    league_id,
                    player_analysis.recommendation,
                    player_analysis.confidence,
                    player_analysis.value_score,
                    factors_json,
                    player_analysis.market_value,
                    player_analysis.trend,
                    None,  # form_trajectory not currently tracked
                    risk_category,
                    datetime.now().isoformat(),
                ),
            )

            return cursor.lastrowid

    def mark_executed(
        self, player_id: str, execution_timestamp: datetime, transaction_type: str  # "BUY"|"SELL"
    ):
        """
        Mark a recommendation as executed

        Args:
            player_id: Player ID
            execution_timestamp: When the trade was executed
            transaction_type: Type of transaction
        """
        with sqlite3.connect(self.db_path) as conn:
            # Find most recent recommendation for this player
            conn.execute(
                """
                UPDATE recommendation_history
                SET was_executed = 1,
                    execution_timestamp = ?
                WHERE player_id = ?
                  AND recommendation = ?
                  AND was_executed = 0
                ORDER BY timestamp DESC
                LIMIT 1
            """,
                (execution_timestamp.isoformat(), player_id, transaction_type),
            )

    def update_outcomes(self, league_id: str, api_client, days_after: int = 30):
        """
        Update outcomes for recommendations by fetching current market values.

        Args:
            league_id: League ID
            api_client: API client (KickbaseAPI instance)
            days_after: Update recommendations older than this many days
        """
        cutoff_date = (datetime.now() - timedelta(days=days_after)).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Get recommendations without outcomes that are old enough
            cursor = conn.execute(
                """
                SELECT id, player_id, player_name, recommendation, market_value, timestamp
                FROM recommendation_history
                WHERE league_id = ?
                  AND timestamp < ?
                  AND outcome_timestamp IS NULL
            """,
                (league_id, cutoff_date),
            )
            recommendations = cursor.fetchall()

        if not recommendations:
            return

        console.print(f"[dim]Updating outcomes for {len(recommendations)} recommendations...[/dim]")

        # Fetch current market values in batch
        player_ids = [rec[1] for rec in recommendations]
        current_values = self._fetch_current_market_values(api_client, league_id, player_ids)

        # Update each recommendation
        updated = 0
        for rec_id, player_id, player_name, rec_type, original_value, timestamp in recommendations:
            try:
                current_value = current_values.get(player_id)

                if current_value is None:
                    continue  # Player no longer available

                # Calculate profit
                profit_amount = current_value - original_value
                profit_pct = (profit_amount / original_value * 100) if original_value > 0 else 0

                # Determine if profitable based on recommendation type
                was_profitable = self._evaluate_outcome(rec_type, profit_pct, timestamp)

                # Update database
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        """
                        UPDATE recommendation_history
                        SET outcome_value = ?,
                            was_profitable = ?,
                            profit_amount = ?,
                            outcome_timestamp = ?
                        WHERE id = ?
                    """,
                        (
                            current_value,
                            1 if was_profitable else 0,
                            profit_amount,
                            datetime.now().isoformat(),
                            rec_id,
                        ),
                    )

                updated += 1

            except Exception as e:
                console.print(
                    f"[dim]Warning: Failed to update outcome for {player_name}: {e}[/dim]"
                )

        if updated > 0:
            console.print(f"[dim]✓ Updated {updated} recommendation outcomes[/dim]")

    def _fetch_current_market_values(
        self, api_client, league_id: str, player_ids: list[str]
    ) -> dict[str, int]:
        """
        Fetch current market values for players.

        Args:
            api_client: KickbaseAPI instance
            league_id: League ID
            player_ids: List of player IDs to fetch

        Returns:
            dict mapping player_id -> current_market_value
        """
        values = {}

        try:
            # Create league object (needed for API calls)
            from .kickbase_client import League

            league = League(id=league_id, name="", creator_id="")

            # Get all market players
            market_players = api_client.get_market(league)

            for player in market_players:
                if player.id in player_ids:
                    values[player.id] = player.market_value

            # Also check squad (for players we bought)
            squad = api_client.get_squad(league)
            for player in squad:
                if player.id in player_ids:
                    values[player.id] = player.market_value

        except Exception as e:
            console.print(f"[yellow]Warning: Could not fetch market values: {e}[/yellow]")

        return values

    def _evaluate_outcome(self, recommendation: str, profit_pct: float, timestamp: str) -> bool:
        """
        Evaluate if a recommendation was successful.

        Success criteria:
        - BUY: Value increased by >5% after 30 days
        - SELL: Avoided >5% value decline OR captured profit
        - HOLD: Maintained value ±5%

        Args:
            recommendation: The recommendation type
            profit_pct: Actual profit percentage
            timestamp: When recommendation was made

        Returns:
            True if recommendation was successful
        """
        if recommendation == "BUY":
            # Success if value increased
            return profit_pct > 5.0

        elif recommendation == "SELL":
            # Success if we would have lost money by keeping
            # OR if we captured significant profit
            return profit_pct < -5.0 or profit_pct > 15.0

        else:  # HOLD
            # Success if value remained relatively stable
            return abs(profit_pct) <= 10.0

    def analyze_factor_performance(self) -> list[FactorPerformance]:
        """
        Analyze performance of each factor

        Returns:
            List of FactorPerformance objects
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get all recommendations with outcomes
            cursor = conn.execute(
                """
                SELECT factors_json, recommendation, was_profitable, confidence
                FROM recommendation_history
                WHERE outcome_timestamp IS NOT NULL
                  AND factors_json IS NOT NULL
            """
            )

            recommendations = cursor.fetchall()

        # Aggregate by factor
        factor_stats = {}  # (factor_name, rec_type) -> stats

        for factors_json, rec_type, was_profitable, confidence in recommendations:
            try:
                factors = json.loads(factors_json)
                for factor in factors:
                    factor_name = factor["name"]
                    factor_points = factor["points"]

                    key = (factor_name, rec_type)
                    if key not in factor_stats:
                        factor_stats[key] = {
                            "total": 0,
                            "successful": 0,
                            "total_points": 0,
                            "confidence_buckets": {70: [], 80: [], 90: []},
                        }

                    stats = factor_stats[key]
                    stats["total"] += 1
                    stats["total_points"] += factor_points

                    if was_profitable:
                        stats["successful"] += 1

                    # Track confidence calibration
                    if confidence >= 0.85:
                        stats["confidence_buckets"][90].append(was_profitable)
                    elif confidence >= 0.75:
                        stats["confidence_buckets"][80].append(was_profitable)
                    elif confidence >= 0.65:
                        stats["confidence_buckets"][70].append(was_profitable)

            except (json.JSONDecodeError, KeyError):
                continue

        # Calculate metrics and update database
        performances = []

        for (factor_name, rec_type), stats in factor_stats.items():
            total = stats["total"]
            successful = stats["successful"]
            win_rate = (successful / total * 100) if total > 0 else 0
            avg_points = stats["total_points"] / total if total > 0 else 0

            # Calculate confidence calibration
            conf_70 = (
                sum(stats["confidence_buckets"][70]) / len(stats["confidence_buckets"][70]) * 100
                if stats["confidence_buckets"][70]
                else None
            )
            conf_80 = (
                sum(stats["confidence_buckets"][80]) / len(stats["confidence_buckets"][80]) * 100
                if stats["confidence_buckets"][80]
                else None
            )
            conf_90 = (
                sum(stats["confidence_buckets"][90]) / len(stats["confidence_buckets"][90]) * 100
                if stats["confidence_buckets"][90]
                else None
            )

            perf = FactorPerformance(
                factor_name=factor_name,
                recommendation_type=rec_type,
                total_recommendations=total,
                successful_outcomes=successful,
                win_rate=win_rate,
                avg_profit_pct=0.0,  # Would need profit data
                avg_factor_score=avg_points,
                confidence_70_accuracy=conf_70,
                confidence_80_accuracy=conf_80,
                confidence_90_accuracy=conf_90,
                last_updated=datetime.now().isoformat(),
            )

            performances.append(perf)

            # Update database
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO factor_attribution
                    (factor_name, recommendation_type, total_recommendations, successful_outcomes,
                     win_rate, avg_profit_pct, avg_factor_score,
                     confidence_70_accuracy, confidence_80_accuracy, confidence_90_accuracy,
                     last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        factor_name,
                        rec_type,
                        total,
                        successful,
                        win_rate,
                        0.0,
                        avg_points,
                        conf_70,
                        conf_80,
                        conf_90,
                        datetime.now().isoformat(),
                    ),
                )

        return performances

    def get_learning_insights(self) -> dict:
        """
        Get actionable insights from learning data

        Returns:
            Dict with insights and recommendations
        """
        performances = self.analyze_factor_performance()

        if not performances:
            return {
                "has_data": False,
                "message": "Not enough recommendation history to generate insights",
            }

        # Find best and worst factors
        buy_factors = [p for p in performances if p.recommendation_type == "BUY"]
        sell_factors = [p for p in performances if p.recommendation_type == "SELL"]

        insights = []

        # Best buy signals
        if buy_factors:
            best_buy = max(buy_factors, key=lambda p: p.win_rate)
            if best_buy.win_rate > 75 and best_buy.total_recommendations >= 5:
                insights.append(
                    f"✓ '{best_buy.factor_name}' is your best buy signal ({best_buy.win_rate:.0f}% win rate)"
                )

        # Best sell signals
        if sell_factors:
            best_sell = max(sell_factors, key=lambda p: p.win_rate)
            if best_sell.win_rate > 75 and best_sell.total_recommendations >= 5:
                insights.append(
                    f"✓ '{best_sell.factor_name}' is your best sell signal ({best_sell.win_rate:.0f}% win rate)"
                )

        # Weak signals
        weak_factors = [p for p in performances if p.win_rate < 50 and p.total_recommendations >= 5]
        for weak in weak_factors[:2]:  # Top 2 worst
            insights.append(
                f"⚠ '{weak.factor_name}' underperforming ({weak.win_rate:.0f}% win rate) - reduce weight"
            )

        # Confidence calibration
        all_conf_70 = [
            p.confidence_70_accuracy for p in performances if p.confidence_70_accuracy is not None
        ]
        if all_conf_70:
            avg_70 = sum(all_conf_70) / len(all_conf_70)
            if abs(avg_70 - 70) > 5:
                insights.append(
                    f"⚠ 70% confidence predictions are actually {avg_70:.0f}% accurate - needs calibration"
                )

        return {
            "has_data": True,
            "total_recommendations": sum(p.total_recommendations for p in performances),
            "insights": insights,
            "performances": performances,
        }

    def display_learning_report(self):
        """Display learning report as rich panel"""
        insights_data = self.get_learning_insights()

        if not insights_data["has_data"]:
            console.print(
                Panel(
                    insights_data["message"], title="📚 Historical Learning", border_style="yellow"
                )
            )
            return

        # Build report
        lines = []
        lines.append("[bold cyan]📚 Historical Learning Report[/bold cyan]")
        lines.append("")
        lines.append(f"Total Recommendations Tracked: {insights_data['total_recommendations']}")
        lines.append("")

        # Show insights
        if insights_data["insights"]:
            lines.append("[bold]💡 Learning Insights:[/bold]")
            for insight in insights_data["insights"]:
                lines.append(f"  {insight}")
        else:
            lines.append("[dim]Not enough data yet for insights[/dim]")

        # Show top factors table
        lines.append("")
        lines.append("[bold]Top Performing Factors:[/bold]")

        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column("Factor", style="cyan")
        table.add_column("Type", style="blue")
        table.add_column("Win Rate", justify="right", style="green")
        table.add_column("Recs", justify="right", style="dim")

        # Sort by win rate
        top_performers = sorted(
            insights_data["performances"], key=lambda p: p.win_rate, reverse=True
        )[:10]

        for perf in top_performers:
            win_color = (
                "green" if perf.win_rate >= 70 else "yellow" if perf.win_rate >= 50 else "red"
            )
            table.add_row(
                perf.factor_name,
                perf.recommendation_type,
                f"[{win_color}]{perf.win_rate:.0f}%[/{win_color}]",
                str(perf.total_recommendations),
            )

        panel = Panel("\n".join(lines), border_style="cyan", padding=(1, 2))

        console.print(panel)
        console.print(table)
