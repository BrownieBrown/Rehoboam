"""Enhanced player comparison and substitution analysis"""

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class PlayerComparison:
    """Head-to-head comparison of two players"""

    player_a_name: str
    player_a_id: str
    player_b_name: str
    player_b_id: str

    # Value metrics comparison
    value_score_diff: float  # A - B
    efficiency_diff: float  # pts/M difference
    price_diff: int  # euros

    # Risk comparison
    risk_diff: str  # "A is riskier", "B is riskier", "Similar risk"
    volatility_diff: float | None

    # Form comparison
    form_diff: str  # "A trending up", "B trending down", etc.
    trend_diff: float  # % trend difference

    # Verdict
    better_player: str  # "A", "B", or "Tie"
    recommendation: str  # "Swap A for B", "Keep A", "Buy both"
    confidence: float
    reason: str


@dataclass
class ReplacementOption:
    """A potential replacement player"""

    player_name: str
    player_id: str
    position: str

    # Comparison to current
    value_score: float
    value_score_diff: float  # vs current
    price: int
    price_diff: int  # vs current

    # Suitability score
    suitability: float  # 0-100, how good a replacement
    upgrade: bool  # True if better than current
    reason: str


@dataclass
class SubstitutionAnalysis:
    """Best replacement options for a player"""

    current_player_name: str
    current_player_id: str
    current_value_score: float
    current_price: int

    replacements: list[ReplacementOption]  # Sorted by suitability


class ComparisonAnalyzer:
    """Analyze and compare players"""

    def __init__(self):
        # Position scarcity multipliers
        self.position_multipliers = {
            "GK": 1.2,  # Scarcity (only need 1)
            "DEF": 1.0,  # Standard (need 3-5)
            "MID": 0.95,  # Abundant
            "FWD": 1.1,  # Scarcity (need 2-3)
        }

    def compare_players(
        self,
        analysis_a,  # PlayerAnalysis
        analysis_b,  # PlayerAnalysis
    ) -> PlayerComparison:
        """
        Head-to-head comparison of two players

        Args:
            analysis_a: PlayerAnalysis for player A
            analysis_b: PlayerAnalysis for player B

        Returns:
            PlayerComparison object
        """
        player_a = analysis_a.player
        player_b = analysis_b.player

        player_a_name = f"{player_a.first_name} {player_a.last_name}"
        player_b_name = f"{player_b.first_name} {player_b.last_name}"

        # Value score comparison
        value_score_diff = analysis_a.value_score - analysis_b.value_score

        # Efficiency comparison
        efficiency_diff = analysis_a.points_per_million - analysis_b.points_per_million

        # Price comparison
        price_diff = analysis_a.current_price - analysis_b.current_price

        # Risk comparison
        risk_diff, volatility_diff = self._compare_risk(
            analysis_a, analysis_b, player_a_name, player_b_name
        )

        # Form comparison
        form_diff, trend_diff = self._compare_form(
            analysis_a, analysis_b, player_a_name, player_b_name
        )

        # Determine better player
        better_player, recommendation, confidence, reason = self._determine_winner(
            value_score_diff=value_score_diff,
            efficiency_diff=efficiency_diff,
            price_diff=price_diff,
            player_a_name=player_a_name,
            player_b_name=player_b_name,
            analysis_a=analysis_a,
            analysis_b=analysis_b,
        )

        return PlayerComparison(
            player_a_name=player_a_name,
            player_a_id=player_a.id,
            player_b_name=player_b_name,
            player_b_id=player_b.id,
            value_score_diff=value_score_diff,
            efficiency_diff=efficiency_diff,
            price_diff=price_diff,
            risk_diff=risk_diff,
            volatility_diff=volatility_diff,
            form_diff=form_diff,
            trend_diff=trend_diff,
            better_player=better_player,
            recommendation=recommendation,
            confidence=confidence,
            reason=reason,
        )

    def _compare_risk(
        self, analysis_a, analysis_b, player_a_name: str, player_b_name: str
    ) -> tuple[str, float | None]:
        """Compare risk levels of two players"""
        risk_a = analysis_a.risk_metrics
        risk_b = analysis_b.risk_metrics

        if not risk_a or not risk_b:
            return "Risk data not available", None

        volatility_diff = risk_a.price_volatility - risk_b.price_volatility

        if abs(volatility_diff) < 3:
            risk_diff = "Similar risk"
        elif volatility_diff > 0:
            risk_diff = (
                f"{player_a_name} is riskier ({risk_a.risk_category} vs {risk_b.risk_category})"
            )
        else:
            risk_diff = (
                f"{player_b_name} is riskier ({risk_b.risk_category} vs {risk_a.risk_category})"
            )

        return risk_diff, volatility_diff

    def _compare_form(
        self, analysis_a, analysis_b, player_a_name: str, player_b_name: str
    ) -> tuple[str, float]:
        """Compare form trends of two players"""
        trend_a_pct = analysis_a.trend_change_pct if analysis_a.trend_change_pct is not None else 0
        trend_b_pct = analysis_b.trend_change_pct if analysis_b.trend_change_pct is not None else 0

        trend_diff = trend_a_pct - trend_b_pct

        if abs(trend_diff) < 3:
            form_diff = "Similar trends"
        elif trend_diff > 10:
            form_diff = f"{player_a_name} trending up strongly (+{trend_a_pct:.1f}%)"
        elif trend_diff > 5:
            form_diff = f"{player_a_name} trending up (+{trend_a_pct:.1f}%)"
        elif trend_diff < -10:
            form_diff = f"{player_b_name} trending up strongly (+{trend_b_pct:.1f}%)"
        elif trend_diff < -5:
            form_diff = f"{player_b_name} trending up (+{trend_b_pct:.1f}%)"
        else:
            form_diff = "Stable trends"

        return form_diff, trend_diff

    def _determine_winner(
        self,
        value_score_diff: float,
        efficiency_diff: float,
        price_diff: int,
        player_a_name: str,
        player_b_name: str,
        analysis_a,
        analysis_b,
    ) -> tuple[str, str, float, str]:
        """
        Determine which player is better

        Returns:
            (better_player, recommendation, confidence, reason)
        """
        # Weighted scoring
        # Value score: 40%
        # Efficiency: 25%
        # Risk: 20%
        # Trend: 15%

        total_score_diff = 0

        # Value score contribution (40%)
        total_score_diff += value_score_diff * 0.4

        # Efficiency contribution (25%)
        total_score_diff += efficiency_diff * 2.5  # Scale to similar range

        # Risk contribution (20%)
        if analysis_a.risk_metrics and analysis_b.risk_metrics:
            # Lower risk = better (invert the score)
            risk_a_score = self._risk_to_score(analysis_a.risk_metrics.risk_category)
            risk_b_score = self._risk_to_score(analysis_b.risk_metrics.risk_category)
            total_score_diff += (risk_a_score - risk_b_score) * 0.2 * 100

        # Trend contribution (15%)
        trend_a = analysis_a.trend_change_pct if analysis_a.trend_change_pct is not None else 0
        trend_b = analysis_b.trend_change_pct if analysis_b.trend_change_pct is not None else 0
        total_score_diff += (trend_a - trend_b) * 0.15 * 10

        # Determine winner
        confidence = min(abs(total_score_diff) / 20, 1.0)  # 20 point difference = 100% confidence

        if total_score_diff > 10:
            better_player = "A"
            recommendation = f"Choose {player_a_name}"
            reason = f"{player_a_name} is better (score: +{value_score_diff:.1f})"
        elif total_score_diff < -10:
            better_player = "B"
            recommendation = f"Choose {player_b_name}"
            reason = f"{player_b_name} is better (score: +{-value_score_diff:.1f})"
        else:
            better_player = "Tie"
            if price_diff > 0:
                recommendation = f"{player_b_name} (cheaper)"
                reason = "Similar quality, B is cheaper"
            elif price_diff < 0:
                recommendation = f"{player_a_name} (cheaper)"
                reason = "Similar quality, A is cheaper"
            else:
                recommendation = "Either player"
                reason = "Very similar players"

        return better_player, recommendation, confidence, reason

    def _risk_to_score(self, risk_category: str) -> float:
        """Convert risk category to score (0-1, higher = less risky)"""
        risk_scores = {
            "Low Risk": 1.0,
            "Medium Risk": 0.6,
            "High Risk": 0.3,
            "Very High Risk": 0.0,
        }
        return risk_scores.get(risk_category, 0.5)

    def find_replacements(
        self,
        current_analysis,  # PlayerAnalysis for current player
        market_analyses: list,  # List of PlayerAnalysis for market players
        max_results: int = 5,
    ) -> SubstitutionAnalysis:
        """
        Find best replacement options for a player

        Args:
            current_analysis: PlayerAnalysis for player to replace
            market_analyses: List of PlayerAnalysis for available players
            max_results: Maximum number of replacements to return

        Returns:
            SubstitutionAnalysis object
        """
        current_player = current_analysis.player
        current_name = f"{current_player.first_name} {current_player.last_name}"
        current_position = current_player.position
        current_price = current_analysis.current_price
        current_value_score = current_analysis.value_score

        # Filter to same position and similar price (±30%)
        price_min = int(current_price * 0.7)
        price_max = int(current_price * 1.3)

        candidates = []
        for analysis in market_analyses:
            # Must be same position
            if analysis.player.position != current_position:
                continue

            # Must be in price range
            if not (price_min <= analysis.current_price <= price_max):
                continue

            # Calculate suitability
            price_diff = analysis.current_price - current_price
            value_score_diff = analysis.value_score - current_value_score

            # Suitability score (0-100)
            # Factors:
            # - Value score improvement: 50%
            # - Efficiency improvement: 25%
            # - Price difference: 15% (prefer cheaper)
            # - Risk: 10% (prefer lower risk)

            suitability = 50  # Base

            # Value score component (0-50)
            if value_score_diff > 20:
                suitability += 50
            elif value_score_diff > 10:
                suitability += 35
            elif value_score_diff > 0:
                suitability += 20
            elif value_score_diff > -10:
                suitability += 10
            else:
                suitability = 0  # Significant downgrade

            # Efficiency component (0-25)
            efficiency_diff = analysis.points_per_million - current_analysis.points_per_million
            if efficiency_diff > 3:
                suitability += 25
            elif efficiency_diff > 1:
                suitability += 15
            elif efficiency_diff > -1:
                suitability += 5

            # Price component (0-15) - prefer cheaper
            price_diff_pct = (price_diff / current_price * 100) if current_price > 0 else 0
            if price_diff_pct < -20:  # Much cheaper
                suitability += 15
            elif price_diff_pct < -10:  # Cheaper
                suitability += 10
            elif price_diff_pct < 10:  # Similar price
                suitability += 5
            # else: more expensive = no bonus

            # Risk component (0-10)
            if analysis.risk_metrics and current_analysis.risk_metrics:
                current_risk_score = self._risk_to_score(
                    current_analysis.risk_metrics.risk_category
                )
                replacement_risk_score = self._risk_to_score(analysis.risk_metrics.risk_category)
                if replacement_risk_score > current_risk_score:
                    suitability += 10  # Less risky
                elif replacement_risk_score == current_risk_score:
                    suitability += 5  # Same risk

            # Determine if upgrade
            upgrade = value_score_diff > 5

            # Reason
            if value_score_diff > 15:
                reason = f"Major upgrade (+{value_score_diff:.1f} value score)"
            elif value_score_diff > 5:
                reason = f"Good upgrade (+{value_score_diff:.1f} value score)"
            elif value_score_diff > 0:
                reason = f"Slight upgrade (+{value_score_diff:.1f} value score)"
            elif value_score_diff > -5:
                reason = "Similar quality"
            else:
                reason = f"Downgrade ({value_score_diff:.1f} value score)"

            player_name = f"{analysis.player.first_name} {analysis.player.last_name}"

            replacement = ReplacementOption(
                player_name=player_name,
                player_id=analysis.player.id,
                position=analysis.player.position,
                value_score=analysis.value_score,
                value_score_diff=value_score_diff,
                price=analysis.current_price,
                price_diff=price_diff,
                suitability=suitability,
                upgrade=upgrade,
                reason=reason,
            )

            candidates.append(replacement)

        # Sort by suitability (descending)
        candidates.sort(key=lambda r: r.suitability, reverse=True)

        return SubstitutionAnalysis(
            current_player_name=current_name,
            current_player_id=current_player.id,
            current_value_score=current_value_score,
            current_price=current_price,
            replacements=candidates[:max_results],
        )

    def display_comparison(self, comparison: PlayerComparison):
        """Display head-to-head comparison"""
        from rich.panel import Panel

        lines = []
        lines.append(
            f"[bold cyan]{comparison.player_a_name}[/bold cyan] vs [bold cyan]{comparison.player_b_name}[/bold cyan]"
        )
        lines.append("")

        # Value metrics
        lines.append("[bold]Value Metrics:[/bold]")
        value_color = (
            "green"
            if comparison.value_score_diff > 5
            else "red" if comparison.value_score_diff < -5 else "yellow"
        )
        lines.append(
            f"  Value Score: [{value_color}]{comparison.value_score_diff:+.1f}[/{value_color}]"
        )

        eff_color = (
            "green"
            if comparison.efficiency_diff > 1
            else "red" if comparison.efficiency_diff < -1 else "yellow"
        )
        lines.append(
            f"  Efficiency: [{eff_color}]{comparison.efficiency_diff:+.1f} pts/M[/{eff_color}]"
        )

        price_color = (
            "green"
            if comparison.price_diff < 0
            else "red" if comparison.price_diff > 0 else "yellow"
        )
        lines.append(f"  Price: [{price_color}]€{comparison.price_diff:+,}[/{price_color}]")

        # Risk & Form
        lines.append("")
        lines.append("[bold]Risk & Form:[/bold]")
        lines.append(f"  Risk: {comparison.risk_diff}")
        lines.append(f"  Form: {comparison.form_diff}")

        # Verdict
        lines.append("")
        verdict_color = "green" if comparison.confidence > 0.7 else "yellow"
        lines.append(
            f"[bold]Verdict:[/bold] [{verdict_color}]{comparison.recommendation}[/{verdict_color}]"
        )
        lines.append(
            f"[dim]{comparison.reason} (confidence: {comparison.confidence*100:.0f}%)[/dim]"
        )

        panel = Panel("\n".join(lines), border_style="cyan", padding=(1, 2))

        console.print(panel)

    def display_substitution_analysis(self, analysis: SubstitutionAnalysis):
        """Display substitution analysis with replacement options"""

        if not analysis.replacements:
            console.print(
                f"[yellow]No suitable replacements found for {analysis.current_player_name}[/yellow]"
            )
            return

        console.print(f"\n[bold]Replacement Options for {analysis.current_player_name}[/bold]")
        console.print(
            f"[dim]Current: €{analysis.current_price:,} | Value Score: {analysis.current_value_score:.1f}[/dim]\n"
        )

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Player", style="cyan")
        table.add_column("Price", justify="right", style="yellow")
        table.add_column("Value Score", justify="right", style="magenta")
        table.add_column("Suitability", justify="right")
        table.add_column("Reason", style="dim")

        for replacement in analysis.replacements:
            # Color code suitability
            if replacement.suitability >= 80:
                suit_color = "green"
            elif replacement.suitability >= 60:
                suit_color = "yellow"
            else:
                suit_color = "red"

            # Format price difference
            price_str = f"€{replacement.price:,}"
            if replacement.price_diff != 0:
                price_color = "green" if replacement.price_diff < 0 else "red"
                price_str += f" ([{price_color}]{replacement.price_diff:+,}[/{price_color}])"

            # Format value score difference
            value_str = f"{replacement.value_score:.1f}"
            if replacement.value_score_diff != 0:
                value_color = "green" if replacement.value_score_diff > 0 else "red"
                value_str += (
                    f" ([{value_color}]{replacement.value_score_diff:+.1f}[/{value_color}])"
                )

            table.add_row(
                replacement.player_name,
                price_str,
                value_str,
                f"[{suit_color}]{replacement.suitability:.0f}/100[/{suit_color}]",
                replacement.reason,
            )

        console.print(table)
