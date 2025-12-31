"""Portfolio-level analysis of squad"""

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

console = Console()


@dataclass
class PortfolioMetrics:
    """Portfolio-level analysis of squad"""

    # Squad summary
    total_players: int
    total_squad_value: int
    total_budget: int
    total_assets: int  # squad_value + budget

    # Diversification
    diversification_score: float  # 0-100, higher = better diversified
    position_concentration: dict[str, float]  # % of value in each position
    team_concentration: dict[str, float]  # % of value from each Bundesliga team
    top_3_concentration: float  # % of value in top 3 most expensive

    # Risk metrics
    portfolio_volatility: float  # Weighted avg of individual volatilities
    portfolio_beta: float | None  # Correlation to market average
    expected_value_30d: int  # Sum of predicted values
    expected_return_30d_pct: float

    # Recommendations
    rebalancing_suggestions: list[str]
    risk_warnings: list[str]


class PortfolioAnalyzer:
    """Analyze squad as a portfolio"""

    def __init__(self):
        pass

    def analyze_portfolio(
        self,
        squad_analyses: list,  # List of PlayerAnalysis for squad
        market_analyses: list,  # List of PlayerAnalysis for market (for beta calculation)
        current_budget: int,
    ) -> PortfolioMetrics:
        """
        Analyze the portfolio

        Args:
            squad_analyses: List of PlayerAnalysis for current squad
            market_analyses: List of PlayerAnalysis for market players
            current_budget: Current available budget

        Returns:
            PortfolioMetrics object
        """
        # Calculate basic metrics
        total_players = len(squad_analyses)
        total_squad_value = sum(a.market_value for a in squad_analyses)
        total_assets = total_squad_value + current_budget

        # Diversification analysis
        position_concentration = self._calculate_position_concentration(
            squad_analyses, total_squad_value
        )
        team_concentration = self._calculate_team_concentration(squad_analyses, total_squad_value)
        top_3_concentration = self._calculate_top_n_concentration(
            squad_analyses, total_squad_value, n=3
        )

        diversification_score = self._calculate_diversification_score(
            position_concentration, team_concentration, top_3_concentration
        )

        # Risk analysis
        portfolio_volatility = self._calculate_portfolio_volatility(squad_analyses)

        # Beta calculation (correlation to market)
        portfolio_beta = self._calculate_portfolio_beta(squad_analyses, market_analyses)

        # Expected value calculation
        expected_value_30d, expected_return_30d_pct = self._calculate_expected_value(
            squad_analyses, total_squad_value
        )

        # Generate recommendations
        rebalancing_suggestions = self._generate_rebalancing_suggestions(
            position_concentration, team_concentration, top_3_concentration
        )

        risk_warnings = self._generate_risk_warnings(
            current_budget, portfolio_volatility, top_3_concentration
        )

        return PortfolioMetrics(
            total_players=total_players,
            total_squad_value=total_squad_value,
            total_budget=current_budget,
            total_assets=total_assets,
            diversification_score=diversification_score,
            position_concentration=position_concentration,
            team_concentration=team_concentration,
            top_3_concentration=top_3_concentration,
            portfolio_volatility=portfolio_volatility,
            portfolio_beta=portfolio_beta,
            expected_value_30d=expected_value_30d,
            expected_return_30d_pct=expected_return_30d_pct,
            rebalancing_suggestions=rebalancing_suggestions,
            risk_warnings=risk_warnings,
        )

    def _calculate_position_concentration(
        self, squad_analyses: list, total_value: int
    ) -> dict[str, float]:
        """Calculate % of squad value in each position"""
        position_values = {}

        for analysis in squad_analyses:
            position = analysis.player.position
            value = analysis.market_value

            if position not in position_values:
                position_values[position] = 0
            position_values[position] += value

        # Convert to percentages
        position_pct = {}
        for position, value in position_values.items():
            position_pct[position] = (value / total_value * 100) if total_value > 0 else 0

        return position_pct

    def _calculate_team_concentration(
        self, squad_analyses: list, total_value: int
    ) -> dict[str, float]:
        """Calculate % of squad value from each team"""
        team_values = {}

        for analysis in squad_analyses:
            # Get team name from player if available
            team_name = (
                getattr(analysis.player, "team_name", None)
                or getattr(analysis.player, "team", None)
                or "Unknown"
            )

            value = analysis.market_value

            if team_name not in team_values:
                team_values[team_name] = 0
            team_values[team_name] += value

        # Convert to percentages
        team_pct = {}
        for team, value in team_values.items():
            team_pct[team] = (value / total_value * 100) if total_value > 0 else 0

        return team_pct

    def _calculate_top_n_concentration(
        self, squad_analyses: list, total_value: int, n: int = 3
    ) -> float:
        """Calculate % of squad value in top N most expensive players"""
        # Sort by market value descending
        sorted_analyses = sorted(squad_analyses, key=lambda a: a.market_value, reverse=True)

        top_n_value = sum(a.market_value for a in sorted_analyses[:n])
        return (top_n_value / total_value * 100) if total_value > 0 else 0

    def _calculate_diversification_score(
        self,
        position_concentration: dict[str, float],
        team_concentration: dict[str, float],
        top_3_concentration: float,
    ) -> float:
        """
        Calculate overall diversification score (0-100)

        Higher score = better diversified
        """
        # Calculate Herfindahl-Hirschman Index (HHI) for positions
        position_hhi = sum(pct**2 for pct in position_concentration.values())

        # Calculate HHI for teams
        team_hhi = sum(pct**2 for pct in team_concentration.values())

        # Normalize HHI to 0-100 scale (lower HHI = more diversified)
        # Perfect diversification across 4 positions = HHI 2500 (25% each)
        # Perfect diversification across 18 teams = HHI 555 (5.5% each)

        position_score = max(0, 100 - (position_hhi - 2500) / 50)
        team_score = max(0, 100 - (team_hhi - 555) / 50)

        # Penalize high top-3 concentration
        # Ideal: top 3 = 30-40% of squad value
        top_3_score = 100
        if top_3_concentration > 50:
            top_3_score = max(0, 100 - (top_3_concentration - 50) * 2)
        elif top_3_concentration < 25:
            top_3_score = max(0, 100 - (25 - top_3_concentration) * 2)

        # Weighted average (40% position, 40% team, 20% top-3)
        diversification_score = position_score * 0.4 + team_score * 0.4 + top_3_score * 0.2

        return diversification_score

    def _calculate_portfolio_volatility(self, squad_analyses: list) -> float:
        """
        Calculate portfolio volatility as weighted average

        Returns:
            Weighted average volatility (%)
        """
        total_value = sum(a.market_value for a in squad_analyses)

        if total_value == 0:
            return 0.0

        weighted_volatility = 0.0

        for analysis in squad_analyses:
            weight = analysis.market_value / total_value

            # Get volatility from risk metrics if available
            volatility = 0.0
            if analysis.risk_metrics:
                volatility = analysis.risk_metrics.price_volatility

            weighted_volatility += weight * volatility

        return weighted_volatility

    def _calculate_portfolio_beta(
        self, squad_analyses: list, market_analyses: list
    ) -> float | None:
        """
        Calculate portfolio beta (correlation to market)

        Beta = Weighted average of individual betas
        (Simplified calculation - proper beta requires covariance)

        Returns:
            Beta value or None if insufficient data
        """
        # This is a simplified beta calculation
        # Proper beta would require historical returns data

        if not market_analyses:
            return None

        total_value = sum(a.market_value for a in squad_analyses)

        if total_value == 0:
            return None

        # Calculate market average volatility
        market_volatilities = [
            a.risk_metrics.price_volatility for a in market_analyses if a.risk_metrics
        ]

        if not market_volatilities:
            return None

        market_avg_volatility = sum(market_volatilities) / len(market_volatilities)

        if market_avg_volatility == 0:
            return 1.0  # Default beta

        # Calculate weighted average volatility for squad
        squad_volatility = self._calculate_portfolio_volatility(squad_analyses)

        # Beta approximation: squad_volatility / market_volatility
        beta = squad_volatility / market_avg_volatility

        return beta

    def _calculate_expected_value(
        self, squad_analyses: list, total_value: int
    ) -> tuple[int, float]:
        """
        Calculate expected portfolio value in 30 days

        Returns:
            (expected_value_30d, expected_return_pct)
        """
        # For now, use a simple approach based on trends
        # In future, could integrate with prediction intervals

        expected_value = total_value  # Start with current value

        for analysis in squad_analyses:
            current_value = analysis.market_value

            # Estimate 30-day change from trend
            change_pct = 0.0

            if analysis.trend and analysis.trend_change_pct is not None:
                # Assume trend continues (with some dampening)
                weekly_change = analysis.trend_change_pct
                monthly_change = weekly_change * 4 * 0.7  # 4 weeks, 70% continuation
                change_pct = monthly_change

            expected_value += int(current_value * change_pct / 100)

        expected_return_pct = (
            ((expected_value - total_value) / total_value * 100) if total_value > 0 else 0
        )

        return expected_value, expected_return_pct

    def _generate_rebalancing_suggestions(
        self,
        position_concentration: dict[str, float],
        team_concentration: dict[str, float],
        top_3_concentration: float,
    ) -> list[str]:
        """Generate rebalancing suggestions"""
        suggestions = []

        # Check position concentration
        for position, pct in position_concentration.items():
            if pct > 40:
                suggestions.append(f"Reduce {position} exposure ({pct:.0f}% → target <40%)")
            elif pct < 15 and position in ["DEF", "MID", "FWD"]:
                suggestions.append(f"Increase {position} exposure ({pct:.0f}% → target >15%)")

        # Check team concentration
        for team, pct in team_concentration.items():
            if pct > 30:
                suggestions.append(f"Reduce {team} exposure ({pct:.0f}% → target <30%)")

        # Check top-3 concentration
        if top_3_concentration > 55:
            suggestions.append(
                f"Top 3 players too concentrated ({top_3_concentration:.0f}%) - consider selling one expensive player"
            )
        elif top_3_concentration < 25:
            suggestions.append(
                f"Top 3 players underweight ({top_3_concentration:.0f}%) - squad lacks star power"
            )

        return suggestions

    def _generate_risk_warnings(
        self, current_budget: int, portfolio_volatility: float, top_3_concentration: float
    ) -> list[str]:
        """Generate risk warnings"""
        warnings = []

        # Budget warnings
        if current_budget < 0:
            warnings.append(f"⚠ Negative budget: €{current_budget:,} - sell players before gameday")
        elif current_budget < 500_000:
            warnings.append(f"⚠ Low budget: €{current_budget:,} - limited trading flexibility")

        # Volatility warnings
        if portfolio_volatility > 25:
            warnings.append(
                f"⚠ High portfolio volatility ({portfolio_volatility:.1f}%) - risky squad"
            )

        # Concentration warnings
        if top_3_concentration > 60:
            warnings.append("⚠ Very concentrated in top 3 players - high dependency risk")

        return warnings

    def display_portfolio_metrics(self, metrics: PortfolioMetrics):
        """Display portfolio metrics as rich panel"""
        lines = []
        lines.append("[bold cyan]📊 Portfolio Analysis[/bold cyan]")
        lines.append("")
        lines.append(f"[bold]Total Assets:[/bold] €{metrics.total_assets:,}")
        lines.append(
            f"  Squad Value: €{metrics.total_squad_value:,} ({metrics.total_players} players)"
        )
        lines.append(f"  Budget: €{metrics.total_budget:,}")
        lines.append("")

        # Diversification
        div_color = (
            "green"
            if metrics.diversification_score >= 70
            else "yellow" if metrics.diversification_score >= 50 else "red"
        )
        div_label = (
            "Excellent"
            if metrics.diversification_score >= 80
            else (
                "Good"
                if metrics.diversification_score >= 60
                else "Fair" if metrics.diversification_score >= 40 else "Poor"
            )
        )

        lines.append(
            f"[bold]Diversification:[/bold] [{div_color}]{metrics.diversification_score:.0f}/100 ({div_label})[/{div_color}]"
        )

        # Position breakdown
        lines.append("")
        lines.append("[bold]Position Concentration:[/bold]")
        for position in ["GK", "DEF", "MID", "FWD"]:
            pct = metrics.position_concentration.get(position, 0)
            color = "yellow" if pct > 40 or pct < 15 else "green"
            lines.append(f"  {position}: [{color}]{pct:.0f}%[/{color}]")

        # Top teams
        lines.append("")
        lines.append("[bold]Top Team Exposures:[/bold]")
        top_teams = sorted(metrics.team_concentration.items(), key=lambda x: x[1], reverse=True)[:3]
        for team, pct in top_teams:
            color = "red" if pct > 30 else "yellow" if pct > 20 else "green"
            lines.append(f"  {team}: [{color}]{pct:.0f}%[/{color}]")

        # Risk metrics
        lines.append("")
        lines.append("[bold]Risk Metrics:[/bold]")
        lines.append(f"  Portfolio Volatility: {metrics.portfolio_volatility:.1f}%")

        if metrics.portfolio_beta is not None:
            beta_label = (
                "Aggressive"
                if metrics.portfolio_beta > 1.1
                else "Defensive" if metrics.portfolio_beta < 0.9 else "Neutral"
            )
            lines.append(f"  Beta: {metrics.portfolio_beta:.2f} ({beta_label})")

        lines.append(f"  Top 3 Concentration: {metrics.top_3_concentration:.0f}%")

        # Projections
        lines.append("")
        lines.append("[bold]30-Day Projection:[/bold]")
        return_color = "green" if metrics.expected_return_30d_pct > 0 else "red"
        lines.append(f"  Expected Value: €{metrics.expected_value_30d:,}")
        lines.append(
            f"  Expected Return: [{return_color}]{metrics.expected_return_30d_pct:+.1f}%[/{return_color}]"
        )

        # Suggestions and warnings
        if metrics.rebalancing_suggestions:
            lines.append("")
            lines.append("[bold]💡 Rebalancing Suggestions:[/bold]")
            for suggestion in metrics.rebalancing_suggestions[:3]:
                lines.append(f"  • {suggestion}")

        if metrics.risk_warnings:
            lines.append("")
            lines.append("[bold red]⚠ Risk Warnings:[/bold red]")
            for warning in metrics.risk_warnings:
                lines.append(f"  {warning}")

        panel = Panel("\n".join(lines), border_style="cyan", padding=(1, 2))

        console.print(panel)
