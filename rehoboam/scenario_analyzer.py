"""Multi-scenario outcome analysis for decisions"""

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

console = Console()


@dataclass
class Scenario:
    """A possible outcome scenario"""

    name: str  # "best", "likely", "worst"
    probability: float  # 0-1

    # Outcomes
    value_30d: int  # Market value in 30 days
    value_change_pct: float
    points_scored: int  # Expected points

    # Assumptions
    assumptions: list[str]  # What must happen for this scenario
    triggers: list[str]  # Events that lead here


@dataclass
class ScenarioAnalysis:
    """Multi-scenario outcome analysis"""

    player_id: str
    player_name: str
    current_value: int
    action: str  # "buy", "sell", "hold"

    scenarios: dict[str, Scenario]  # "best", "likely", "worst"
    expected_value: float  # Probability-weighted average
    risk_adjusted_value: float  # EV adjusted for risk tolerance

    recommendation: str
    confidence: float


class ScenarioAnalyzer:
    """Analyze multiple outcome scenarios"""

    def __init__(self, risk_tolerance: float = 0.5):
        """
        Initialize scenario analyzer

        Args:
            risk_tolerance: 0-1, where 0 = risk-averse, 1 = risk-seeking
        """
        self.risk_tolerance = risk_tolerance

    def analyze_scenarios(
        self,
        player_analysis,  # PlayerAnalysis
        action: str = "buy",  # "buy", "sell", or "hold"
    ) -> ScenarioAnalysis:
        """
        Generate multi-scenario analysis for a decision

        Args:
            player_analysis: PlayerAnalysis object
            action: Action being considered

        Returns:
            ScenarioAnalysis object
        """
        player = player_analysis.player
        player_name = f"{player.first_name} {player.last_name}"
        current_value = player_analysis.market_value

        # Generate scenarios
        scenarios = {}

        # Best case scenario (20% probability)
        scenarios["best"] = self._generate_best_case(player_analysis)

        # Likely case scenario (60% probability)
        scenarios["likely"] = self._generate_likely_case(player_analysis)

        # Worst case scenario (20% probability)
        scenarios["worst"] = self._generate_worst_case(player_analysis)

        # Calculate expected value (probability-weighted)
        expected_value = (
            scenarios["best"].value_30d * scenarios["best"].probability
            + scenarios["likely"].value_30d * scenarios["likely"].probability
            + scenarios["worst"].value_30d * scenarios["worst"].probability
        )

        # Calculate risk-adjusted value
        risk_adjusted_value = self._calculate_risk_adjusted_value(
            scenarios=scenarios,
            expected_value=expected_value,
            risk_tolerance=self.risk_tolerance,
        )

        # Determine recommendation
        recommendation, confidence = self._determine_recommendation(
            action=action,
            scenarios=scenarios,
            expected_value=expected_value,
            risk_adjusted_value=risk_adjusted_value,
            current_value=current_value,
        )

        return ScenarioAnalysis(
            player_id=player.id,
            player_name=player_name,
            current_value=current_value,
            action=action,
            scenarios=scenarios,
            expected_value=expected_value,
            risk_adjusted_value=risk_adjusted_value,
            recommendation=recommendation,
            confidence=confidence,
        )

    def _generate_best_case(self, player_analysis) -> Scenario:
        """Generate best case scenario (20% probability)"""
        current_value = player_analysis.market_value

        # Best case assumptions
        assumptions = [
            "Player stays healthy",
            "Team has easy fixtures",
            "Form improves significantly",
            "Market trend continues upward",
        ]

        triggers = [
            "Strong performances (15+ points per game)",
            "Team wins next 3 games",
            "Price momentum accelerates",
        ]

        # Calculate best case value
        # Take current trend and amplify it
        trend_pct = player_analysis.trend_change_pct if player_analysis.trend_change_pct else 0

        # Best case: trend continues at 150% for 30 days
        best_multiplier = 1.5
        if trend_pct > 0:
            # Positive trend amplifies
            value_change_pct = trend_pct * best_multiplier * 2  # 30 days vs 14 days
        else:
            # Negative trend reverses
            value_change_pct = abs(trend_pct) * 0.5  # Recovery

        # Cap at reasonable limits
        value_change_pct = min(value_change_pct, 30.0)  # Max 30% gain

        value_30d = int(current_value * (1 + value_change_pct / 100))

        # Expected points (optimistic)
        points_scored = int(player_analysis.points * 1.3)  # 30% better than current

        return Scenario(
            name="Best Case",
            probability=0.20,
            value_30d=value_30d,
            value_change_pct=value_change_pct,
            points_scored=points_scored,
            assumptions=assumptions,
            triggers=triggers,
        )

    def _generate_likely_case(self, player_analysis) -> Scenario:
        """Generate likely case scenario (60% probability)"""
        current_value = player_analysis.market_value

        # Likely case assumptions
        assumptions = [
            "Normal performance continues",
            "No major injuries",
            "Typical fixture difficulty",
            "Market behaves normally",
        ]

        triggers = [
            "Consistent performances",
            "Team maintains form",
            "Standard market conditions",
        ]

        # Calculate likely value (base prediction)
        trend_pct = player_analysis.trend_change_pct if player_analysis.trend_change_pct else 0

        # Likely case: trend continues at normal rate with some mean reversion
        value_change_pct = trend_pct * 1.8  # 14-day to ~30-day projection

        # Add mean reversion
        if player_analysis.vs_peak_pct and player_analysis.vs_peak_pct < -20:
            # Below peak = some recovery expected
            value_change_pct += abs(player_analysis.vs_peak_pct) * 0.08

        # Dampen extreme predictions
        value_change_pct = value_change_pct * 0.8

        value_30d = int(current_value * (1 + value_change_pct / 100))

        # Expected points (realistic)
        points_scored = player_analysis.points

        return Scenario(
            name="Likely Case",
            probability=0.60,
            value_30d=value_30d,
            value_change_pct=value_change_pct,
            points_scored=points_scored,
            assumptions=assumptions,
            triggers=triggers,
        )

    def _generate_worst_case(self, player_analysis) -> Scenario:
        """Generate worst case scenario (20% probability)"""
        current_value = player_analysis.market_value

        # Worst case assumptions
        assumptions = [
            "Injury occurs",
            "Team loses key games",
            "Form drops significantly",
            "Market turns negative",
        ]

        triggers = [
            "Poor performances (0-5 points per game)",
            "Team on losing streak",
            "Price momentum reverses",
        ]

        # Calculate worst case value
        trend_pct = player_analysis.trend_change_pct if player_analysis.trend_change_pct else 0

        # Worst case: negative trend or reversal
        if trend_pct > 0:
            # Positive trend reverses
            value_change_pct = -trend_pct * 0.7
        else:
            # Negative trend accelerates
            value_change_pct = trend_pct * 2.0

        # Minimum floor (don't predict total collapse)
        value_change_pct = max(value_change_pct, -25.0)  # Max 25% loss

        value_30d = int(current_value * (1 + value_change_pct / 100))

        # Expected points (pessimistic)
        points_scored = int(player_analysis.points * 0.5)  # 50% worse

        return Scenario(
            name="Worst Case",
            probability=0.20,
            value_30d=value_30d,
            value_change_pct=value_change_pct,
            points_scored=points_scored,
            assumptions=assumptions,
            triggers=triggers,
        )

    def _calculate_risk_adjusted_value(
        self,
        scenarios: dict[str, Scenario],
        expected_value: float,
        risk_tolerance: float,
    ) -> float:
        """
        Calculate risk-adjusted expected value

        Args:
            scenarios: Dict of scenarios
            expected_value: Probability-weighted EV
            risk_tolerance: 0-1, where 0 = risk-averse, 1 = risk-seeking

        Returns:
            Risk-adjusted value
        """
        # Calculate volatility (standard deviation of outcomes)
        values = [s.value_30d for s in scenarios.values()]
        probabilities = [s.probability for s in scenarios.values()]

        # Weighted variance
        weighted_mean = sum(v * p for v, p in zip(values, probabilities, strict=False))
        weighted_variance = sum(
            p * (v - weighted_mean) ** 2 for v, p in zip(values, probabilities, strict=False)
        )
        volatility = weighted_variance**0.5

        # Risk premium based on volatility and risk tolerance
        # Risk-averse (0.0): high penalty for volatility
        # Risk-seeking (1.0): no penalty
        risk_premium = volatility * (1 - risk_tolerance)

        # Adjust expected value by risk premium
        risk_adjusted_value = expected_value - risk_premium

        return risk_adjusted_value

    def _determine_recommendation(
        self,
        action: str,
        scenarios: dict[str, Scenario],
        expected_value: float,
        risk_adjusted_value: float,
        current_value: int,
    ) -> tuple[str, float]:
        """
        Determine recommendation based on scenarios

        Returns:
            (recommendation, confidence)
        """
        # Calculate expected return
        expected_return_pct = (
            ((expected_value - current_value) / current_value * 100) if current_value > 0 else 0
        )

        risk_adjusted_return_pct = (
            ((risk_adjusted_value - current_value) / current_value * 100)
            if current_value > 0
            else 0
        )

        # Upside/downside ratio
        upside = scenarios["best"].value_change_pct
        downside = scenarios["worst"].value_change_pct

        upside_downside_ratio = abs(upside / downside) if downside != 0 else float("inf")

        # Determine recommendation
        if action == "buy":
            if risk_adjusted_return_pct > 10 and upside_downside_ratio > 2:
                # Strong buy: good risk-adjusted return and upside dominates downside
                return f"STRONG BUY (+{risk_adjusted_return_pct:.1f}% expected)", 0.9
            elif risk_adjusted_return_pct > 5:
                return f"BUY (+{risk_adjusted_return_pct:.1f}% expected)", 0.75
            elif expected_return_pct > 3:
                return f"CONSIDER BUYING (+{expected_return_pct:.1f}% expected)", 0.6
            else:
                return "AVOID (Limited upside)", 0.7

        elif action == "sell":
            if risk_adjusted_return_pct < -5 and abs(downside) > upside:
                # Strong sell: negative outlook and downside dominates
                return f"STRONG SELL ({risk_adjusted_return_pct:.1f}% expected)", 0.9
            elif risk_adjusted_return_pct < -2:
                return f"SELL ({risk_adjusted_return_pct:.1f}% expected)", 0.75
            elif expected_return_pct < 0:
                return f"CONSIDER SELLING ({expected_return_pct:.1f}% expected)", 0.6
            else:
                return "HOLD (Positive outlook)", 0.7

        else:  # hold
            if abs(expected_return_pct) < 3:
                return "HOLD (Neutral outlook)", 0.6
            elif expected_return_pct > 0:
                return f"HOLD (Slight upside +{expected_return_pct:.1f}%)", 0.7
            else:
                return f"HOLD (Slight downside {expected_return_pct:.1f}%)", 0.7

    def display_scenario_analysis(self, analysis: ScenarioAnalysis):
        """Display multi-scenario analysis"""
        lines = []
        lines.append(f"[bold cyan]🎲 Scenario Analysis: {analysis.player_name}[/bold cyan]")
        lines.append(f"[dim]Current Value: €{analysis.current_value:,}[/dim]")
        lines.append("")

        # Display each scenario
        for scenario_name in ["best", "likely", "worst"]:
            scenario = analysis.scenarios[scenario_name]

            # Choose emoji and color
            if scenario_name == "best":
                emoji = "🎯"
                color = "green"
            elif scenario_name == "likely":
                emoji = "➡️ "
                color = "yellow"
            else:
                emoji = "⚠️ "
                color = "red"

            change_color = "green" if scenario.value_change_pct > 0 else "red"

            lines.append(
                f"[bold]{emoji} {scenario.name} ({scenario.probability*100:.0f}% chance):[/bold]"
            )
            lines.append(
                f"  €{analysis.current_value:,} → €{scenario.value_30d:,} ([{change_color}]{scenario.value_change_pct:+.1f}%[/{change_color}])"
            )
            lines.append(f"  [dim]Assumes: {', '.join(scenario.assumptions[:2])}[/dim]")
            lines.append("")

        # Expected values
        ev_change_pct = (
            ((analysis.expected_value - analysis.current_value) / analysis.current_value * 100)
            if analysis.current_value > 0
            else 0
        )
        ev_color = "green" if ev_change_pct > 0 else "red"

        lines.append(
            f"[bold]Expected Value:[/bold] €{int(analysis.expected_value):,} ([{ev_color}]{ev_change_pct:+.1f}%[/{ev_color}])"
        )

        rav_change_pct = (
            ((analysis.risk_adjusted_value - analysis.current_value) / analysis.current_value * 100)
            if analysis.current_value > 0
            else 0
        )
        rav_color = "green" if rav_change_pct > 0 else "red"

        lines.append(
            f"[bold]Risk-Adjusted:[/bold] €{int(analysis.risk_adjusted_value):,} ([{rav_color}]{rav_change_pct:+.1f}%[/{rav_color}])"
        )

        # Recommendation
        lines.append("")
        rec_color = (
            "green"
            if "BUY" in analysis.recommendation or "HOLD" in analysis.recommendation
            else "red"
        )
        lines.append(
            f"[bold]Recommendation:[/bold] [{rec_color}]{analysis.recommendation}[/{rec_color}]"
        )
        lines.append(f"[dim](Confidence: {analysis.confidence*100:.0f}%)[/dim]")

        # Upside/Downside comparison
        upside = analysis.scenarios["best"].value_change_pct
        downside = analysis.scenarios["worst"].value_change_pct
        lines.append("")
        lines.append(f"[dim]Upside: +{upside:.1f}% | Downside: {downside:.1f}%[/dim]")

        panel = Panel("\n".join(lines), border_style="cyan", padding=(1, 2))

        console.print(panel)
