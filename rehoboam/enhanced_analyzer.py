"""Enhanced analysis with predictions, comparisons, and strategic insights"""

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@dataclass
class PredictionInterval:
    """Confidence interval for a prediction"""

    point_estimate: float  # Best guess (%)
    lower_bound: float  # Lower bound of interval (%)
    upper_bound: float  # Upper bound of interval (%)
    confidence_level: float  # 0.70, 0.80, 0.90, 0.95
    interval_width: float  # upper - lower (uncertainty measure)


@dataclass
class PlayerPrediction:
    """Prediction for a player's future performance"""

    player_id: str
    player_name: str

    # Trend-based predictions (backward compatible)
    predicted_value_7d: int  # Predicted market value in 7 days
    predicted_value_14d: int  # Predicted market value in 14 days
    predicted_value_30d: int  # Predicted market value in 30 days

    value_change_7d_pct: float  # Expected % change
    value_change_14d_pct: float
    value_change_30d_pct: float

    # NEW: Prediction intervals with uncertainty ranges
    predicted_value_7d_interval: PredictionInterval | None = None
    predicted_value_14d_interval: PredictionInterval | None = None
    predicted_value_30d_interval: PredictionInterval | None = None

    # Performance predictions
    form_trajectory: str = "stable"  # "improving", "declining", "stable", "volatile"
    injury_risk: str = "low"  # "low", "medium", "high"

    # Confidence
    prediction_confidence: float = 0.5  # 0-1, how confident we are
    data_quality: str = "fair"  # "excellent", "good", "fair", "poor"

    # Supporting data
    recent_trend_pct: float = 0.0
    long_term_trend_pct: float = 0.0
    games_played: int = 0
    consistency_score: float = 0.5


@dataclass
class PositionComparison:
    """Comparison of players in the same position"""

    position: str
    players: list  # List of player analysis with rankings
    average_value_score: float
    average_pts_per_million: float

    # Position-specific insights
    top_performer: str  # Name of top player
    best_value: str  # Name of best value player
    rising_star: str  # Name of player trending up


@dataclass
class SquadBalance:
    """Analysis of squad balance and composition"""

    # Position distribution
    goalkeepers: int
    defenders: int
    midfielders: int
    forwards: int

    # Quality distribution
    elite_players: int  # Value score >= 80
    solid_players: int  # Value score 60-79
    average_players: int  # Value score 40-59
    weak_players: int  # Value score < 40

    # Value distribution
    total_squad_value: int
    avg_player_value: int
    most_valuable_player: str
    least_valuable_player: str

    # Balance recommendations
    position_needs: list  # Positions that need strengthening
    budget_allocation: dict  # Recommended budget per position


class EnhancedAnalyzer:
    """Enhanced analyzer with predictions and comparisons"""

    def __init__(self):
        self.console = Console()

    def _calculate_prediction_intervals(
        self,
        current_value: int,
        momentum: float,
        volatility: float,
        data_quality: str,
        games_played: int,
        confidence_level: float = 0.70,
        num_simulations: int = 1000,
    ) -> dict[str, PredictionInterval]:
        """
        Calculate confidence intervals using Monte Carlo simulation

        Args:
            current_value: Current market value
            momentum: Weekly momentum (%)
            volatility: Price volatility (%)
            data_quality: Data quality ("excellent", "good", "fair", "poor")
            games_played: Number of games played
            confidence_level: Confidence level (0.70, 0.80, etc.)
            num_simulations: Number of Monte Carlo simulations

        Returns:
            Dict with keys '7d', '14d', '30d' mapping to PredictionInterval objects
        """
        import random

        # Adjust uncertainty based on data quality
        uncertainty_multiplier = {
            "excellent": 1.0,
            "good": 1.2,
            "fair": 1.5,
            "poor": 2.0,
        }.get(data_quality, 1.5)

        # Additional uncertainty for small sample sizes
        if games_played < 5:
            uncertainty_multiplier *= 2.0
        elif games_played < 10:
            uncertainty_multiplier *= 1.3

        # Volatility affects spread
        if volatility > 40:
            uncertainty_multiplier *= 1.3
        elif volatility > 25:
            uncertainty_multiplier *= 1.15

        results = {}

        for horizon_days, horizon_label in [(7, "7d"), (14, "14d"), (30, "30d")]:
            simulated_changes = []

            for _ in range(num_simulations):
                # Sample momentum from normal distribution
                # Mean = momentum, StdDev = volatility adjusted for horizon
                momentum_sample = random.gauss(
                    momentum * (horizon_days / 7),  # Scale to horizon
                    volatility
                    * uncertainty_multiplier
                    * (horizon_days / 7) ** 0.5,  # Square root of time
                )

                # Sample mean reversion component
                mean_reversion = random.gauss(0, volatility * 0.3)

                # Combined prediction
                total_change_pct = momentum_sample + mean_reversion

                # Cap extreme values
                total_change_pct = max(-50, min(100, total_change_pct))

                simulated_changes.append(total_change_pct)

            # Sort results
            simulated_changes.sort()

            # Extract percentiles for confidence interval
            lower_percentile = (1 - confidence_level) / 2
            upper_percentile = 1 - lower_percentile

            lower_idx = int(lower_percentile * num_simulations)
            upper_idx = int(upper_percentile * num_simulations)
            median_idx = num_simulations // 2

            lower_bound = simulated_changes[lower_idx]
            upper_bound = simulated_changes[upper_idx]
            point_estimate = simulated_changes[median_idx]  # Median

            interval = PredictionInterval(
                point_estimate=point_estimate,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                confidence_level=confidence_level,
                interval_width=upper_bound - lower_bound,
            )

            results[horizon_label] = interval

        return results

    def predict_player_value(
        self,
        player,
        trend_data: dict,
        performance_data: dict = None,
        matchup_context: dict = None,
        predict_points: bool = False,
    ) -> PlayerPrediction:
        """
        Predict future player value based on trends, fixtures, and seasonal patterns

        Args:
            player: Player object
            trend_data: Historical trend data
            performance_data: Performance stats
            matchup_context: Matchup and SOS data (for fixture weighting)

        Returns:
            PlayerPrediction object
        """
        from .value_calculator import PlayerValue

        current_value = player.market_value
        player_name = f"{player.first_name} {player.last_name}"

        # Extract trend information
        recent_trend_pct = trend_data.get("trend_pct", 0) if trend_data.get("has_data") else 0

        # Get long-term trend (30-day) if available
        long_term_trend_pct = (
            trend_data.get("long_term_pct", 0) if trend_data.get("has_data") else 0
        )

        # Also calculate vs peak
        vs_peak_pct = 0
        if trend_data.get("has_data"):
            peak_value = trend_data.get("peak_value", current_value)
            if peak_value > 0 and peak_value != current_value:
                vs_peak_pct = ((current_value - peak_value) / peak_value) * 100

        # Momentum-based prediction with mean reversion
        # Recent trend momentum (14-day trend projected forward)
        weekly_momentum = recent_trend_pct / 14 * 7  # Convert 14-day trend to 7-day

        # PHASE 3 ENHANCEMENT: Better recovery trajectory for players below peak
        # Players far below peak have higher recovery potential
        mean_reversion_boost = 0
        if vs_peak_pct < -40:
            # Very far below peak = strong recovery potential
            mean_reversion_boost = min(abs(vs_peak_pct) * 0.15, 8.0)  # Enhanced from 0.1, 5.0
        elif vs_peak_pct < -30:
            # Far below peak = good recovery potential
            mean_reversion_boost = min(abs(vs_peak_pct) * 0.12, 6.0)
        elif vs_peak_pct < -15:
            # Moderately below peak = some recovery potential
            mean_reversion_boost = min(abs(vs_peak_pct) * 0.08, 3.0)

        # Combine momentum with mean reversion
        # 7-day: mostly momentum
        pred_7d_pct = weekly_momentum
        # 14-day: momentum + some mean reversion
        pred_14d_pct = (weekly_momentum * 1.8) + (mean_reversion_boost * 0.3)
        # 30-day: momentum + more mean reversion
        pred_30d_pct = (weekly_momentum * 3.5) + (mean_reversion_boost * 1.0)

        # PHASE 3 ENHANCEMENT: Fixture-weighted predictions
        # Adjust based on upcoming fixture difficulty (SOS)
        fixture_adjustment = 0
        if matchup_context and matchup_context.get("has_data"):
            sos_data = matchup_context.get("sos")
            if sos_data:
                sos_rating = getattr(sos_data, "sos_rating", None)
                if sos_rating == "Very Easy":
                    # Easy fixtures = expect value increase from good performances
                    fixture_adjustment = 3.0
                elif sos_rating == "Easy":
                    fixture_adjustment = 1.5
                elif sos_rating == "Difficult":
                    fixture_adjustment = -1.5
                elif sos_rating == "Very Difficult":
                    # Hard fixtures = expect value decrease from poor performances
                    fixture_adjustment = -3.0

        # Apply fixture adjustment (more impact on longer horizons)
        pred_7d_pct += fixture_adjustment * 0.3
        pred_14d_pct += fixture_adjustment * 0.5
        pred_30d_pct += fixture_adjustment * 1.0

        # PHASE 3 ENHANCEMENT: Seasonal patterns
        # Different positions peak at different times
        from datetime import datetime

        days_into_season = (datetime.now() - datetime(datetime.now().year, 8, 1)).days
        days_until_season_end = (datetime(datetime.now().year + 1, 5, 31) - datetime.now()).days

        seasonal_adjustment = 0
        position = player.position

        if position == "FWD" and days_until_season_end < 60:
            # Forwards peak in spring (goals matter more in title race)
            seasonal_adjustment = 2.0
        elif position == "DEF" and days_into_season < 60:
            # Defenders start strong (clean sheets early season)
            seasonal_adjustment = 1.5
        elif position == "MID" and 60 <= days_into_season <= 180:
            # Midfielders consistent mid-season
            seasonal_adjustment = 1.0

        # Apply seasonal adjustment (only to 30-day predictions)
        pred_30d_pct += seasonal_adjustment

        # Apply predictions
        pred_7d = current_value * (1 + pred_7d_pct / 100)
        pred_14d = current_value * (1 + pred_14d_pct / 100)
        pred_30d = current_value * (1 + pred_30d_pct / 100)

        # Calculate percentage changes
        change_7d = ((pred_7d - current_value) / current_value) * 100
        change_14d = ((pred_14d - current_value) / current_value) * 100
        change_30d = ((pred_30d - current_value) / current_value) * 100

        # Determine form trajectory (based on both recent and long-term trends)
        if recent_trend_pct > 10 and long_term_trend_pct > 5:
            form_trajectory = "improving"  # Strong recent + positive long-term
        elif recent_trend_pct > 15:
            form_trajectory = "improving"  # Very strong recent trend
        elif recent_trend_pct < -10 and long_term_trend_pct < -5:
            form_trajectory = "declining"  # Falling on both timeframes
        elif recent_trend_pct < -15:
            form_trajectory = "declining"  # Sharp recent decline
        elif abs(recent_trend_pct - long_term_trend_pct) > 20:
            form_trajectory = "volatile"  # Big mismatch between short and long term
        else:
            form_trajectory = "stable"  # Consistent trends

        # Calculate base prediction confidence
        confidence = 0.5  # Base confidence
        if trend_data.get("has_data"):
            confidence += 0.2
        if performance_data:
            confidence += 0.2
        if abs(recent_trend_pct) > 20:
            confidence -= 0.1  # High volatility = lower confidence
        confidence = max(0.1, min(1.0, confidence))

        # PHASE 3 ENHANCEMENT: Injury risk from consistency drops
        injury_risk = "low"  # Default
        if performance_data:
            player_value = PlayerValue.calculate(player, performance_data=performance_data)

            # Check for sharp drop in consistency (injury indicator)
            if player_value.consistency_score is not None:
                if player_value.consistency_score < 0.2:
                    # Very inconsistent = high injury risk
                    injury_risk = "high"
                    # Reduce prediction confidence
                    confidence *= 0.6
                elif player_value.consistency_score < 0.4:
                    # Inconsistent = medium injury risk
                    injury_risk = "medium"
                    confidence *= 0.8

            # Also check if they have very few games played (might be injured/returning)
            if player_value.games_played is not None and player_value.games_played < 3:
                if injury_risk == "low":  # Don't downgrade from high
                    injury_risk = "medium"
                confidence *= 0.7

        # Data quality assessment
        if performance_data and trend_data.get("has_data"):
            data_quality = "excellent"
        elif trend_data.get("has_data"):
            data_quality = "good"
        elif performance_data:
            data_quality = "fair"
        else:
            data_quality = "poor"

        # Get games played and consistency
        games_played = 0
        consistency = 0.5
        if performance_data:
            player_value = PlayerValue.calculate(player, performance_data=performance_data)
            games_played = player_value.games_played or 0
            consistency = player_value.consistency_score or 0.5

        # Calculate prediction intervals using Monte Carlo simulation
        # Estimate price volatility from trend volatility (if available)
        price_volatility = 20.0  # Default assumption
        if abs(recent_trend_pct) > 5:
            # Higher volatility if prices are moving
            price_volatility = abs(recent_trend_pct) * 1.5

        intervals = self._calculate_prediction_intervals(
            current_value=current_value,
            momentum=weekly_momentum,
            volatility=price_volatility,
            data_quality=data_quality,
            games_played=games_played,
            confidence_level=0.70,  # 70% confidence intervals
        )

        # Points prediction: estimate expected matchday points
        expected_points = None
        if predict_points and performance_data:
            avg_pts = player.average_points
            # Base: average points
            expected_pts = avg_pts

            # Form adjustment: recent performance vs average
            if player.points > 0 and avg_pts > 0:
                form_ratio = player.points / avg_pts
                if form_ratio > 1.5:
                    expected_pts *= 1.15  # Hot streak boost
                elif form_ratio < 0.5:
                    expected_pts *= 0.85  # Cold streak penalty

            # Fixture adjustment
            if fixture_adjustment > 0:
                expected_pts *= 1.1  # Easy fixture boost
            elif fixture_adjustment < 0:
                expected_pts *= 0.9  # Hard fixture penalty

            # Consistency adjustment
            if consistency > 0.7:
                expected_pts *= 1.05  # Reliable scorer
            elif consistency < 0.3:
                expected_pts *= 0.9  # Unreliable

            expected_points = round(expected_pts, 1)

        prediction = PlayerPrediction(
            player_id=player.id,
            player_name=player_name,
            predicted_value_7d=int(pred_7d),
            predicted_value_14d=int(pred_14d),
            predicted_value_30d=int(pred_30d),
            value_change_7d_pct=change_7d,
            value_change_14d_pct=change_14d,
            value_change_30d_pct=change_30d,
            predicted_value_7d_interval=intervals["7d"],
            predicted_value_14d_interval=intervals["14d"],
            predicted_value_30d_interval=intervals["30d"],
            form_trajectory=form_trajectory,
            injury_risk=injury_risk,
            prediction_confidence=confidence,
            data_quality=data_quality,
            recent_trend_pct=recent_trend_pct,
            long_term_trend_pct=long_term_trend_pct,
            games_played=games_played,
            consistency_score=consistency,
        )

        # Store expected points in metadata-like attribute
        if expected_points is not None:
            prediction.expected_points = expected_points

        return prediction

    def compare_similar_players(self, player, all_analyses: list, top_n: int = 5) -> list:
        """
        Find and compare similar players (same position, similar value)

        Args:
            player: Player to compare
            all_analyses: List of all PlayerAnalysis objects
            top_n: Number of similar players to return

        Returns:
            List of similar player analyses, sorted by value score
        """
        # Filter by same position
        same_position = [
            a
            for a in all_analyses
            if a.player.position == player.position and a.player.id != player.id
        ]

        if not same_position:
            return []

        # Find players in similar price range (±30%)
        target_price = player.market_value
        price_min = target_price * 0.7
        price_max = target_price * 1.3

        similar = [a for a in same_position if price_min <= a.market_value <= price_max]

        # Sort by value score
        similar.sort(key=lambda a: a.value_score, reverse=True)

        return similar[:top_n]

    def analyze_position_landscape(self, analyses: list) -> dict:
        """
        Analyze the landscape for each position

        Returns:
            Dict mapping position -> PositionComparison
        """
        positions = {}

        for analysis in analyses:
            pos = analysis.player.position
            if pos not in positions:
                positions[pos] = []
            positions[pos].append(analysis)

        comparisons = {}

        for pos, player_analyses in positions.items():
            if not player_analyses:
                continue

            # Calculate averages
            avg_value_score = sum(a.value_score for a in player_analyses) / len(player_analyses)
            avg_pts_per_million = sum(a.points_per_million for a in player_analyses) / len(
                player_analyses
            )

            # Find top performers
            top_by_value = max(player_analyses, key=lambda a: a.value_score)
            top_by_efficiency = max(player_analyses, key=lambda a: a.points_per_million)

            # Find rising star (best trend)
            rising = None
            best_trend = -100
            for a in player_analyses:
                if a.trend_change_pct and a.trend_change_pct > best_trend:
                    best_trend = a.trend_change_pct
                    rising = a

            comparisons[pos] = PositionComparison(
                position=pos,
                players=sorted(player_analyses, key=lambda a: a.value_score, reverse=True),
                average_value_score=avg_value_score,
                average_pts_per_million=avg_pts_per_million,
                top_performer=f"{top_by_value.player.first_name} {top_by_value.player.last_name}",
                best_value=f"{top_by_efficiency.player.first_name} {top_by_efficiency.player.last_name}",
                rising_star=(
                    f"{rising.player.first_name} {rising.player.last_name}" if rising else "N/A"
                ),
            )

        return comparisons

    def analyze_squad_balance(self, squad_analyses: list) -> SquadBalance:
        """
        Analyze squad composition and balance

        Args:
            squad_analyses: List of PlayerAnalysis for owned players

        Returns:
            SquadBalance object
        """
        # Count by position
        gk = len([a for a in squad_analyses if a.player.position == "Goalkeeper"])
        df = len([a for a in squad_analyses if a.player.position == "Defender"])
        mf = len([a for a in squad_analyses if a.player.position == "Midfielder"])
        fw = len([a for a in squad_analyses if a.player.position == "Forward"])

        # Count by quality
        elite = len([a for a in squad_analyses if a.value_score >= 80])
        solid = len([a for a in squad_analyses if 60 <= a.value_score < 80])
        average = len([a for a in squad_analyses if 40 <= a.value_score < 60])
        weak = len([a for a in squad_analyses if a.value_score < 40])

        # Calculate value metrics
        total_value = sum(a.market_value for a in squad_analyses)
        avg_value = total_value // len(squad_analyses) if squad_analyses else 0

        most_valuable = (
            max(squad_analyses, key=lambda a: a.market_value) if squad_analyses else None
        )
        least_valuable = (
            min(squad_analyses, key=lambda a: a.market_value) if squad_analyses else None
        )

        # Determine position needs (basic heuristic)
        position_needs = []
        if gk < 1:
            position_needs.append("Goalkeeper (need at least 1)")
        if df < 3:
            position_needs.append(f"Defender (have {df}, recommend 3-5)")
        if mf < 3:
            position_needs.append(f"Midfielder (have {mf}, recommend 3-5)")
        if fw < 2:
            position_needs.append(f"Forward (have {fw}, recommend 2-3)")

        # Budget allocation (simple equal split for now)
        budget_allocation = {
            "Goalkeeper": 0.15,
            "Defender": 0.30,
            "Midfielder": 0.35,
            "Forward": 0.20,
        }

        return SquadBalance(
            goalkeepers=gk,
            defenders=df,
            midfielders=mf,
            forwards=fw,
            elite_players=elite,
            solid_players=solid,
            average_players=average,
            weak_players=weak,
            total_squad_value=total_value,
            avg_player_value=avg_value,
            most_valuable_player=(
                f"{most_valuable.player.first_name} {most_valuable.player.last_name}"
                if most_valuable
                else "N/A"
            ),
            least_valuable_player=(
                f"{least_valuable.player.first_name} {least_valuable.player.last_name}"
                if least_valuable
                else "N/A"
            ),
            position_needs=position_needs,
            budget_allocation=budget_allocation,
        )

    def display_predictions(self, predictions: list[PlayerPrediction], title: str = ""):
        """Display value predictions for players"""

        table = Table(title=title if title else None, show_header=True)
        table.add_column("Player", style="cyan", width=20)
        table.add_column("Form", width=12)
        table.add_column("7-Day Prediction", justify="right", width=35)
        table.add_column("14-Day Prediction", justify="right", width=35)
        table.add_column("30-Day Prediction", justify="right", width=35)
        table.add_column("Confidence", justify="center", width=12)

        for pred in predictions[:10]:  # Show top 10
            # Color code form trajectory
            form_colors = {
                "improving": "green",
                "stable": "yellow",
                "declining": "red",
                "volatile": "magenta",
            }
            form_icons = {"improving": "📈", "stable": "➡️", "declining": "📉", "volatile": "🌊"}
            form_color = form_colors.get(pred.form_trajectory, "white")
            form_icon = form_icons.get(pred.form_trajectory, "")
            form_str = f"[{form_color}]{form_icon} {pred.form_trajectory}[/{form_color}]"

            # Color code predictions based on change (with intervals if available)
            def format_prediction_with_interval(change_pct, interval: PredictionInterval | None):
                if interval:
                    # Show range with point estimate
                    if change_pct > 5:
                        color = "green"
                    elif change_pct < -5:
                        color = "red"
                    else:
                        color = "yellow"

                    # Format: +5% (+2% to +8%)
                    return f"[{color}]{change_pct:+.1f}% ({interval.lower_bound:+.1f}% to {interval.upper_bound:+.1f}%)[/{color}]"
                else:
                    # Fallback to old format
                    if change_pct > 5:
                        return f"[green]+{change_pct:.1f}%[/green]"
                    elif change_pct < -5:
                        return f"[red]{change_pct:.1f}%[/red]"
                    else:
                        return f"[yellow]{change_pct:+.1f}%[/yellow]"

            # Confidence indicator
            conf_pct = int(pred.prediction_confidence * 100)
            if conf_pct >= 70:
                conf_str = f"[green]{conf_pct}% ✓[/green]"
            elif conf_pct >= 50:
                conf_str = f"[yellow]{conf_pct}%[/yellow]"
            else:
                conf_str = f"[red]{conf_pct}% ⚠[/red]"

            table.add_row(
                pred.player_name,
                form_str,
                format_prediction_with_interval(
                    pred.value_change_7d_pct, pred.predicted_value_7d_interval
                ),
                format_prediction_with_interval(
                    pred.value_change_14d_pct, pred.predicted_value_14d_interval
                ),
                format_prediction_with_interval(
                    pred.value_change_30d_pct, pred.predicted_value_30d_interval
                ),
                conf_str,
            )

        console.print(table)

        # Add explanation panel if intervals are shown
        if predictions and predictions[0].predicted_value_7d_interval:
            console.print("\n[dim]📊 Prediction Guide:[/dim]")
            console.print(
                "[dim]  Ranges show 70% confidence intervals (Monte Carlo simulation)[/dim]"
            )
            console.print(
                "[dim]  ✓ Narrow range = High confidence  |  ⚠ Wide range = High uncertainty[/dim]"
            )

    def display_position_comparison(self, position_comparisons: dict):
        """Display position-by-position comparison"""

        console.print("\n[bold cyan]📊 Position Analysis[/bold cyan]\n")

        for pos, comp in position_comparisons.items():
            panel_content = f"""[bold]Average Value Score:[/bold] {comp.average_value_score:.1f}/100
[bold]Average Efficiency:[/bold] {comp.average_pts_per_million:.1f} pts/M€
[bold]Players Analyzed:[/bold] {len(comp.players)}

[green]🏆 Top Performer:[/green] {comp.top_performer}
[green]💰 Best Value:[/green] {comp.best_value}
[yellow]⭐ Rising Star:[/yellow] {comp.rising_star}"""

            panel = Panel(panel_content, title=f"[bold]{pos}[/bold]", border_style="cyan")
            console.print(panel)

    def display_squad_balance(self, balance: SquadBalance, current_budget: int = 0):
        """Display squad balance analysis"""

        console.print("\n[bold cyan]⚖️  Squad Balance & Composition[/bold cyan]\n")

        # Position distribution
        position_table = Table(title="Position Distribution", show_header=True)
        position_table.add_column("Position", style="cyan")
        position_table.add_column("Count", justify="center")
        position_table.add_column("Status", justify="center")

        positions = [
            ("Goalkeeper", balance.goalkeepers, 1, 2),
            ("Defender", balance.defenders, 3, 5),
            ("Midfielder", balance.midfielders, 3, 5),
            ("Forward", balance.forwards, 2, 3),
        ]

        for pos_name, count, min_rec, max_rec in positions:
            if count < min_rec:
                status = f"[red]⚠️  Need {min_rec - count} more[/red]"
            elif count > max_rec:
                status = f"[yellow]⚡ {count - max_rec} extra[/yellow]"
            else:
                status = "[green]✓ Good[/green]"

            position_table.add_row(pos_name, str(count), status)

        console.print(position_table)

        # Quality distribution
        quality_table = Table(title="Quality Distribution", show_header=True)
        quality_table.add_column("Tier", style="cyan")
        quality_table.add_column("Count", justify="center")
        quality_table.add_column("Percentage", justify="right")

        total_players = (
            balance.elite_players
            + balance.solid_players
            + balance.average_players
            + balance.weak_players
        )

        quality_tiers = [
            ("Elite (80+)", balance.elite_players, "green"),
            ("Solid (60-79)", balance.solid_players, "yellow"),
            ("Average (40-59)", balance.average_players, "white"),
            ("Weak (<40)", balance.weak_players, "red"),
        ]

        for tier_name, count, color in quality_tiers:
            pct = (count / total_players * 100) if total_players > 0 else 0
            quality_table.add_row(
                tier_name, f"[{color}]{count}[/{color}]", f"[{color}]{pct:.1f}%[/{color}]"
            )

        console.print("\n")
        console.print(quality_table)

        # Value summary
        console.print("\n[bold]💰 Financial Summary:[/bold]")
        console.print(f"  Total Squad Value: €{balance.total_squad_value:,}")
        console.print(f"  Average Player Value: €{balance.avg_player_value:,}")
        console.print(f"  Most Valuable: {balance.most_valuable_player}")
        console.print(f"  Least Valuable: {balance.least_valuable_player}")
        if current_budget > 0:
            console.print(f"  Current Budget: €{current_budget:,}")

        # Recommendations
        if balance.position_needs:
            console.print("\n[bold yellow]⚠️  Position Needs:[/bold yellow]")
            for need in balance.position_needs:
                console.print(f"  • {need}")
