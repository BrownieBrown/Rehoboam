"""Learn optimal factor weights from historical outcomes"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console

from .analyzer import FactorWeights

console = Console()


@dataclass
class WeightOptimizationResult:
    """Result of weight optimization"""

    weight_name: str
    category: str  # "BUY" or "SELL"
    old_value: float
    new_value: float
    change_pct: float
    win_rate: float
    sample_size: int
    confidence: float  # 0-1, how confident we are
    method: str  # "baseline_optimization", "gradient_update", "bayesian"


@dataclass
class LearningMetrics:
    """Metrics for evaluating learning performance"""

    total_recommendations: int
    with_outcomes: int
    overall_win_rate: float
    baseline_win_rate: float  # Using default weights
    improvement_pct: float
    weights_learned: int
    last_optimization: str
    confidence_calibration: dict[str, float]  # 70/80/90% accuracy


class FactorWeightLearner:
    """
    Learn optimal factor weights from historical recommendation outcomes.

    Uses hybrid learning approach:
    1. Baseline optimization: Use all historical data to find optimal starting weights
    2. Gradual adaptation: Update weights incrementally with rolling 30-day windows
    """

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path("logs") / "bid_learning.db"

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

        # Learning hyperparameters
        self.min_sample_size = 15  # Minimum recommendations before updating
        self.max_weight_change_pct = 50.0  # Maximum % change per update
        self.learning_rate = 0.15  # How aggressively to adjust (0-1)
        self.confidence_threshold = 0.6  # Minimum confidence to apply changes

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            # Table: learned_factor_weights
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learned_factor_weights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    weight_name TEXT NOT NULL,
                    category TEXT NOT NULL,

                    current_value REAL NOT NULL,
                    baseline_value REAL NOT NULL,

                    sample_size INTEGER NOT NULL,
                    win_rate REAL,
                    avg_profit_pct REAL,
                    confidence_score REAL,

                    optimization_method TEXT,
                    last_updated TEXT NOT NULL,
                    update_count INTEGER DEFAULT 1,

                    rolling_30d_win_rate REAL,
                    rolling_30d_profit_pct REAL,
                    performance_vs_baseline REAL,

                    UNIQUE(weight_name, category)
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_weights_category
                ON learned_factor_weights(category)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_weights_updated
                ON learned_factor_weights(last_updated)
            """
            )

            # Table: weight_optimization_history
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weight_optimization_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    optimization_id TEXT NOT NULL,
                    weight_name TEXT NOT NULL,
                    category TEXT NOT NULL,

                    old_value REAL NOT NULL,
                    new_value REAL NOT NULL,
                    change_pct REAL NOT NULL,

                    optimization_method TEXT NOT NULL,
                    sample_size INTEGER NOT NULL,
                    win_rate_before REAL,
                    win_rate_after REAL,

                    timestamp TEXT NOT NULL
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opt_history_timestamp
                ON weight_optimization_history(timestamp)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opt_history_opt_id
                ON weight_optimization_history(optimization_id)
            """
            )

    def get_current_weights(self, category: str | None = None) -> FactorWeights:
        """
        Get current learned weights.

        Args:
            category: Optional category filter ("BUY" or "SELL")

        Returns:
            FactorWeights with learned values (or defaults if not learned)
        """
        with sqlite3.connect(self.db_path) as conn:
            if category:
                cursor = conn.execute(
                    """
                    SELECT weight_name, current_value
                    FROM learned_factor_weights
                    WHERE category = ?
                    """,
                    (category,),
                )
            else:
                cursor = conn.execute(
                    "SELECT weight_name, current_value FROM learned_factor_weights"
                )

            learned = {row[0]: row[1] for row in cursor.fetchall()}

        # Merge with defaults
        defaults = FactorWeights()
        for weight_name, value in learned.items():
            if hasattr(defaults, weight_name):
                setattr(defaults, weight_name, value)

        return defaults

    def should_run_optimization(self) -> dict[str, bool]:
        """
        Determine if optimization should run.

        Returns:
            dict with keys "baseline_needed", "incremental_needed"
        """
        with sqlite3.connect(self.db_path) as conn:
            # Check if baseline optimization has been run
            cursor = conn.execute("SELECT COUNT(*) FROM learned_factor_weights")
            learned_count = cursor.fetchone()[0]

            baseline_needed = learned_count == 0

            # Check if incremental update is needed (>7 days since last)
            if not baseline_needed:
                cursor = conn.execute(
                    """
                    SELECT MAX(last_updated) FROM learned_factor_weights
                    """
                )
                last_updated = cursor.fetchone()[0]

                if last_updated:
                    try:
                        last_dt = datetime.fromisoformat(last_updated)
                        days_since = (datetime.now() - last_dt).days
                        incremental_needed = days_since >= 7
                    except (ValueError, TypeError):
                        incremental_needed = True
                else:
                    incremental_needed = True
            else:
                incremental_needed = False

        return {"baseline_needed": baseline_needed, "incremental_needed": incremental_needed}

    def get_learning_metrics(self) -> LearningMetrics:
        """Get comprehensive learning performance metrics"""
        with sqlite3.connect(self.db_path) as conn:
            # Total recommendations
            cursor = conn.execute("SELECT COUNT(*) FROM recommendation_history")
            total = cursor.fetchone()[0]

            # With outcomes
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM recommendation_history
                WHERE outcome_timestamp IS NOT NULL
                """
            )
            with_outcomes = cursor.fetchone()[0]

            # Overall win rate
            cursor = conn.execute(
                """
                SELECT AVG(CAST(was_profitable AS REAL)) FROM recommendation_history
                WHERE was_profitable IS NOT NULL
                """
            )
            result = cursor.fetchone()[0]
            overall_win_rate = (result or 0) * 100

            # Learned weights count
            cursor = conn.execute("SELECT COUNT(*) FROM learned_factor_weights")
            weights_learned = cursor.fetchone()[0]

            # Last optimization
            cursor = conn.execute("SELECT MAX(last_updated) FROM learned_factor_weights")
            last_opt = cursor.fetchone()[0] or "Never"

        # Calculate confidence calibration
        calibration = self._calculate_confidence_calibration()

        return LearningMetrics(
            total_recommendations=total,
            with_outcomes=with_outcomes,
            overall_win_rate=overall_win_rate,
            baseline_win_rate=50.0,  # Would need A/B test to measure
            improvement_pct=overall_win_rate - 50.0,
            weights_learned=weights_learned,
            last_optimization=last_opt,
            confidence_calibration=calibration,
        )

    def _calculate_confidence_calibration(self) -> dict[str, float]:
        """Calculate how well confidence scores match actual outcomes"""
        with sqlite3.connect(self.db_path) as conn:
            calibration = {}

            for threshold_name, min_conf, max_conf in [
                ("70%", 0.65, 0.75),
                ("80%", 0.75, 0.85),
                ("90%", 0.85, 1.0),
            ]:
                cursor = conn.execute(
                    """
                    SELECT AVG(CAST(was_profitable AS REAL)) * 100
                    FROM recommendation_history
                    WHERE confidence >= ? AND confidence < ?
                      AND was_profitable IS NOT NULL
                    """,
                    (min_conf, max_conf),
                )
                result = cursor.fetchone()[0]
                calibration[threshold_name] = result if result is not None else 0.0

        return calibration

    def optimize_baseline_weights(
        self, category: str, min_samples: int = 30
    ) -> tuple[FactorWeights, list[WeightOptimizationResult]]:
        """
        Optimize all weights using entire historical dataset.

        Algorithm: Grid search over weight values
        - Tests multiple weight values (0.5x to 2.0x current)
        - Objective = win_rate * 0.7 + normalized_profit * 0.3
        - Selects weight that maximizes objective

        Args:
            category: "BUY" or "SELL"
            min_samples: Minimum recommendations needed per factor

        Returns:
            Optimized FactorWeights and list of optimization results
        """
        import uuid

        # Load all recommendations with outcomes
        recommendations = self._load_recommendations_with_outcomes(category)

        if len(recommendations) < min_samples:
            console.print(
                f"[yellow]Not enough data for baseline optimization "
                f"({len(recommendations)} < {min_samples})[/yellow]"
            )
            return FactorWeights(), []

        console.print(
            f"[cyan]Optimizing {category} weights with {len(recommendations)} recommendations...[/cyan]"
        )

        # Analyze factor performance
        factor_stats = self._analyze_factor_contributions(recommendations, category)

        # Optimize each weight
        optimized_weights = {}
        optimization_results = []
        optimization_id = str(uuid.uuid4())[:8]

        for weight_name, stats in factor_stats.items():
            if stats["sample_size"] < min_samples:
                if stats["sample_size"] > 0:
                    console.print(
                        f"[dim]  Skipping {weight_name}: only {stats['sample_size']} samples[/dim]"
                    )
                continue

            # Get default value for this weight
            defaults = FactorWeights()
            current_value = getattr(defaults, weight_name, 1.0)

            # Optimize this weight
            optimal_weight, result = self._optimize_single_weight(
                weight_name, stats, category, current_value, "baseline_optimization"
            )

            optimized_weights[weight_name] = optimal_weight
            optimization_results.append(result)

            console.print(
                f"[dim]  ✓ {weight_name}: {current_value:.1f} → {optimal_weight:.1f} "
                f"({result.change_pct:+.0f}%, win_rate={result.win_rate:.0f}%)[/dim]"
            )

        # Create FactorWeights with optimized values
        learned_weights = self._create_factor_weights_from_learned(optimized_weights, category)

        # Persist to database
        self._save_learned_weights(optimization_results, optimization_id)

        console.print(
            f"[green]✓ Optimized {len(optimization_results)} weights for {category}[/green]"
        )

        return learned_weights, optimization_results

    def update_weights_incrementally(
        self, category: str, window_days: int = 30
    ) -> tuple[FactorWeights, list[WeightOptimizationResult]]:
        """
        Update weights using recent data (last N days).

        Algorithm: Gradient descent with bounds
        - Uses rolling window (default 30 days)
        - Calculates gradient based on win rate vs target
        - new_weight = current * (1 + gradient * learning_rate)
        - Bounded to ±50% change

        Args:
            category: "BUY" or "SELL"
            window_days: Rolling window for recent data

        Returns:
            Updated FactorWeights and optimization results
        """
        import uuid

        # Load recent recommendations
        cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()
        recommendations = self._load_recommendations_with_outcomes(category, since=cutoff)

        if len(recommendations) < self.min_sample_size:
            # Not enough recent data
            return self.get_current_weights(category), []

        console.print(
            f"[cyan]Updating {category} weights with {len(recommendations)} recent recommendations...[/cyan]"
        )

        # Analyze recent performance
        factor_stats = self._analyze_factor_contributions(recommendations, category)

        # Load current learned weights
        current_weights_obj = self.get_current_weights(category)

        # Update each weight using gradient descent
        optimization_results = []
        optimization_id = str(uuid.uuid4())[:8]

        for weight_name, stats in factor_stats.items():
            if stats["sample_size"] < self.min_sample_size:
                continue

            current_value = getattr(current_weights_obj, weight_name, 1.0)

            # Calculate gradient based on performance
            gradient = self._calculate_weight_gradient(stats)

            # Apply update with learning rate and bounds
            new_value = self._apply_gradient_update(current_value, gradient, self.learning_rate)

            # Calculate change and confidence
            change_pct = ((new_value - current_value) / abs(current_value)) * 100
            confidence = self._calculate_update_confidence(stats)

            # Only apply if change is significant and confidence is high
            if abs(change_pct) > 5.0 and confidence >= self.confidence_threshold:
                result = WeightOptimizationResult(
                    weight_name=weight_name,
                    category=category,
                    old_value=current_value,
                    new_value=new_value,
                    change_pct=change_pct,
                    win_rate=stats["win_rate"],
                    sample_size=stats["sample_size"],
                    confidence=confidence,
                    method="gradient_update",
                )
                optimization_results.append(result)

                console.print(
                    f"[dim]  ✓ {weight_name}: {current_value:.1f} → {new_value:.1f} "
                    f"({change_pct:+.0f}%, confidence={confidence:.0%})[/dim]"
                )

        # Apply updates and persist
        if optimization_results:
            self._save_learned_weights(optimization_results, optimization_id)

            # Create updated weights object
            updated_weights_dict = {r.weight_name: r.new_value for r in optimization_results}
            updated_weights = self._create_factor_weights_from_learned(
                updated_weights_dict, category
            )

            console.print(f"[green]✓ Updated {len(optimization_results)} weights[/green]")

            return updated_weights, optimization_results
        else:
            console.print("[dim]No significant weight changes needed[/dim]")
            return current_weights_obj, []

    def _calculate_weight_gradient(self, stats: dict) -> float:
        """
        Calculate gradient for weight adjustment.

        Gradient is based on:
        1. Win rate deviation from target (65%)
        2. Profit percentage contribution

        Args:
            stats: Factor performance statistics

        Returns:
            Gradient value (-1.0 to +1.0)
        """
        target_win_rate = 65.0  # Target 65% win rate
        target_profit = 10.0  # Target 10% profit

        # Win rate component (primary)
        wr_diff = stats["win_rate"] - target_win_rate
        wr_gradient = wr_diff / 100.0 * 2.0  # Scale to ~(-1, 1)

        # Profit component (secondary)
        profit_diff = (stats.get("avg_profit_pct", 0) - target_profit) / 10.0
        profit_gradient = profit_diff * 1.0

        # Combined gradient (weighted)
        gradient = wr_gradient * 0.7 + profit_gradient * 0.3

        # Clamp to [-1, 1]
        return max(-1.0, min(1.0, gradient))

    def _apply_gradient_update(
        self, current: float, gradient: float, learning_rate: float
    ) -> float:
        """
        Apply gradient update with bounds checking.

        Update rule: new_value = current_value * (1 + gradient * learning_rate)

        Args:
            current: Current weight value
            gradient: Gradient (-1 to 1)
            learning_rate: Learning rate (0 to 1)

        Returns:
            New weight value with bounds applied
        """
        # Calculate change factor
        change_factor = 1.0 + (gradient * learning_rate)

        # Apply change
        new_value = current * change_factor

        # Apply maximum change bounds (±50%)
        max_change = abs(current) * (self.max_weight_change_pct / 100.0)
        new_value = max(current - max_change, min(current + max_change, new_value))

        # Apply absolute bounds
        new_value = max(0.1, min(100.0, new_value))

        return new_value

    def _calculate_update_confidence(self, stats: dict) -> float:
        """
        Calculate confidence in weight update (0-1).

        Based on:
        1. Sample size (higher = more confident)
        2. Win rate variance (lower = more confident)
        3. Statistical significance

        Args:
            stats: Factor performance statistics

        Returns:
            Confidence score (0-1)
        """
        # Sample size component (50+ samples = full confidence)
        sample_confidence = min(1.0, stats["sample_size"] / 50.0)

        # Variance component (estimate from outcomes)
        outcomes = stats.get("outcomes", [])
        if len(outcomes) > 1:
            # Calculate win rate variance
            profitable_count = sum(1 for o in outcomes if o["profitable"])
            win_rate = profitable_count / len(outcomes)
            # Use binomial variance formula
            variance = win_rate * (1 - win_rate)
            # Lower variance = higher confidence
            variance_confidence = max(0.0, 1.0 - (variance * 4))  # Scale to 0-1
        else:
            variance_confidence = 0.5

        # Statistical significance component
        # Use simple binomial test: is win rate significantly different from 50%?
        win_rate = stats["win_rate"]
        sample_size = stats["sample_size"]

        # Approximate z-score for binomial proportion
        if sample_size > 0:
            expected = 0.5
            observed = win_rate / 100.0
            std_error = (expected * (1 - expected) / sample_size) ** 0.5
            if std_error > 0:
                z_score = abs(observed - expected) / std_error
                # z > 1.96 = 95% significant, z > 1.65 = 90% significant
                significance_confidence = min(1.0, z_score / 2.0)
            else:
                significance_confidence = 0.5
        else:
            significance_confidence = 0.0

        # Combined confidence (weighted average)
        confidence = (
            sample_confidence * 0.5 + variance_confidence * 0.3 + significance_confidence * 0.2
        )

        return confidence

    # Private helper methods for optimization

    def _load_recommendations_with_outcomes(
        self, category: str, since: str | None = None
    ) -> list[dict]:
        """
        Load recommendations with factors and outcomes.

        Args:
            category: "BUY" or "SELL"
            since: Optional cutoff date (ISO format)

        Returns:
            List of recommendation dicts with factors_json and outcome data
        """
        with sqlite3.connect(self.db_path) as conn:
            if since:
                cursor = conn.execute(
                    """
                    SELECT factors_json, was_profitable, profit_amount, market_value
                    FROM recommendation_history
                    WHERE recommendation = ?
                      AND outcome_timestamp IS NOT NULL
                      AND timestamp >= ?
                    """,
                    (category, since),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT factors_json, was_profitable, profit_amount, market_value
                    FROM recommendation_history
                    WHERE recommendation = ?
                      AND outcome_timestamp IS NOT NULL
                    """,
                    (category,),
                )

            rows = cursor.fetchall()

        import json

        recommendations = []
        for factors_json, was_profitable, profit_amount, market_value in rows:
            if not factors_json:
                continue

            try:
                factors = json.loads(factors_json)
                recommendations.append(
                    {
                        "factors": factors,
                        "was_profitable": bool(was_profitable),
                        "profit_amount": profit_amount or 0,
                        "market_value": market_value or 0,
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue

        return recommendations

    def _analyze_factor_contributions(
        self, recommendations: list[dict], category: str
    ) -> dict[str, dict]:
        """
        Analyze how each factor performed across recommendations.

        Args:
            recommendations: List of recommendation dicts
            category: "BUY" or "SELL"

        Returns:
            Dict mapping factor_name -> performance stats
        """
        factor_stats = {}

        for rec in recommendations:
            was_profitable = rec["was_profitable"]
            profit_amount = rec["profit_amount"]
            market_value = rec["market_value"]

            for factor in rec["factors"]:
                factor_name = factor.get("name", "")
                factor_points = factor.get("points", 0)

                if not factor_name:
                    continue

                # Map factor name to weight name
                weight_name = self._factor_name_to_weight_name(factor_name)
                if not weight_name:
                    continue

                if weight_name not in factor_stats:
                    factor_stats[weight_name] = {
                        "sample_size": 0,
                        "successes": 0,
                        "total_profit": 0,
                        "total_points": 0,
                        "outcomes": [],
                    }

                stats = factor_stats[weight_name]
                stats["sample_size"] += 1
                stats["total_points"] += abs(factor_points)

                if was_profitable:
                    stats["successes"] += 1

                # Track profit as percentage
                if market_value > 0:
                    profit_pct = (profit_amount / market_value) * 100
                    stats["total_profit"] += profit_pct
                    stats["outcomes"].append(
                        {"profitable": was_profitable, "profit_pct": profit_pct}
                    )

        # Calculate aggregated metrics
        for _weight_name, stats in factor_stats.items():
            sample_size = stats["sample_size"]
            if sample_size > 0:
                stats["win_rate"] = (stats["successes"] / sample_size) * 100
                stats["avg_profit_pct"] = stats["total_profit"] / sample_size
                stats["avg_points"] = stats["total_points"] / sample_size
            else:
                stats["win_rate"] = 0
                stats["avg_profit_pct"] = 0
                stats["avg_points"] = 0

        return factor_stats

    def _factor_name_to_weight_name(self, factor_name: str) -> str | None:
        """
        Map factor display name to FactorWeights attribute name.

        Args:
            factor_name: Display name from ScoringFactor

        Returns:
            Weight attribute name or None if not mappable
        """
        # Map common factor names to weight names
        mapping = {
            "Base Value": "base_value",
            "Rising Trend": "trend_rising",
            "Falling Trend": "trend_falling",
            "Easy Matchup": "matchup_easy",
            "Hard Matchup": "matchup_hard",
            "Schedule Bonus": "sos_bonus",
            "Market Discount": "discount",
            "Profit Target": "profit_target",
            "Stop Loss": "stop_loss",
            "Peak Decline": "peak_decline",
            "Poor Performance": "poor_performance",
            "Best 11 Protection": "best_eleven_protection",
            "Difficult Schedule": "difficult_schedule",
        }

        return mapping.get(factor_name)

    def _optimize_single_weight(
        self,
        weight_name: str,
        stats: dict,
        category: str,
        current_value: float,
        method: str,
    ) -> tuple[float, WeightOptimizationResult]:
        """
        Optimize a single weight value using grid search.

        Args:
            weight_name: Name of weight to optimize
            stats: Performance statistics for this factor
            category: "BUY" or "SELL"
            current_value: Current weight value
            method: Optimization method name

        Returns:
            Tuple of (optimal_weight, optimization_result)
        """
        import numpy as np

        # Define search space
        search_space = np.linspace(max(0.1, current_value * 0.5), current_value * 2.0, num=20)

        best_objective = -float("inf")
        best_weight = current_value

        # Grid search
        for candidate_weight in search_space:
            # Calculate objective for this weight
            # Objective = win_rate * 0.7 + normalized_profit * 0.3
            win_rate = stats["win_rate"] / 100.0  # Normalize to 0-1
            profit_pct = stats["avg_profit_pct"]

            # Normalize profit to 0-1 (assume -20% to +30% range)
            normalized_profit = (profit_pct + 20) / 50
            normalized_profit = max(0, min(1, normalized_profit))

            objective = win_rate * 0.7 + normalized_profit * 0.3

            if objective > best_objective:
                best_objective = objective
                best_weight = candidate_weight

        # Calculate confidence based on sample size
        confidence = min(1.0, stats["sample_size"] / 50.0)

        # Apply confidence threshold
        if confidence < self.confidence_threshold:
            # Not confident enough - use current value
            best_weight = current_value

        # Calculate change percentage
        change_pct = ((best_weight - current_value) / abs(current_value)) * 100

        result = WeightOptimizationResult(
            weight_name=weight_name,
            category=category,
            old_value=current_value,
            new_value=best_weight,
            change_pct=change_pct,
            win_rate=stats["win_rate"],
            sample_size=stats["sample_size"],
            confidence=confidence,
            method=method,
        )

        return best_weight, result

    def _create_factor_weights_from_learned(
        self, learned: dict[str, float], category: str
    ) -> FactorWeights:
        """
        Create FactorWeights instance with learned values.

        Args:
            learned: Dict mapping weight_name -> optimized_value
            category: "BUY" or "SELL" (for filtering relevant weights)

        Returns:
            FactorWeights with learned values
        """
        weights = FactorWeights()

        for weight_name, value in learned.items():
            if hasattr(weights, weight_name):
                setattr(weights, weight_name, value)

        return weights

    def _save_learned_weights(self, results: list[WeightOptimizationResult], optimization_id: str):
        """
        Persist learned weights to database.

        Args:
            results: List of optimization results
            optimization_id: UUID for this optimization batch
        """
        with sqlite3.connect(self.db_path) as conn:
            for result in results:
                # Insert or update learned weight
                conn.execute(
                    """
                    INSERT OR REPLACE INTO learned_factor_weights
                    (weight_name, category, current_value, baseline_value,
                     sample_size, win_rate, avg_profit_pct, confidence_score,
                     optimization_method, last_updated, update_count,
                     rolling_30d_win_rate, rolling_30d_profit_pct, performance_vs_baseline)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            COALESCE((SELECT update_count FROM learned_factor_weights
                                      WHERE weight_name = ? AND category = ?), 0) + 1,
                            ?, ?, ?)
                    """,
                    (
                        result.weight_name,
                        result.category,
                        result.new_value,
                        result.old_value,  # Store original as baseline
                        result.sample_size,
                        result.win_rate,
                        0.0,  # avg_profit_pct (could calculate from stats)
                        result.confidence,
                        result.method,
                        datetime.now().isoformat(),
                        result.weight_name,
                        result.category,
                        result.win_rate,  # rolling win rate (same for baseline)
                        0.0,  # rolling profit pct
                        0.0,  # performance vs baseline (calculate in incremental)
                    ),
                )

                # Record in history
                conn.execute(
                    """
                    INSERT INTO weight_optimization_history
                    (optimization_id, weight_name, category, old_value, new_value,
                     change_pct, optimization_method, sample_size, win_rate_before,
                     win_rate_after, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        optimization_id,
                        result.weight_name,
                        result.category,
                        result.old_value,
                        result.new_value,
                        result.change_pct,
                        result.method,
                        result.sample_size,
                        50.0,  # Assume 50% baseline before optimization
                        result.win_rate,
                        datetime.now().isoformat(),
                    ),
                )
