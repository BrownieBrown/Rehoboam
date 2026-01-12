"""Opportunity cost analysis for trading decisions"""

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

console = Console()


@dataclass
class OpportunityCost:
    """Trade-off analysis for a potential purchase"""

    target_player_name: str  # Player to buy
    target_player_id: str
    target_cost: int  # Price of target player

    # What must be sold
    players_to_sell: list[tuple[str, int]]  # List of (player_name, price) tuples
    sell_proceeds: int  # Total from sales

    # Net impact
    net_budget_change: int  # New budget - old budget
    net_value_score_change: float  # Target score - sum(sold scores)
    net_efficiency_change: float  # Target pts/M - sum(sold pts/M)
    net_risk_change: str | None  # Risk comparison

    # Assessment
    is_worthwhile: bool
    reason: str  # Explanation
    confidence: float  # 0-1


class OpportunityCostAnalyzer:
    """Analyzes trade-offs for potential purchases"""

    def __init__(self, min_squad_size: int = 11):
        self.min_squad_size = min_squad_size

    def analyze_buy_impact(
        self,
        target_analysis,  # PlayerAnalysis for player to buy
        current_squad: list,  # List of Player objects
        squad_analyses: list,  # List of PlayerAnalysis for squad
        current_budget: int,
    ) -> OpportunityCost | None:
        """
        Analyze the opportunity cost of buying a player

        Args:
            target_analysis: PlayerAnalysis for the player to buy
            current_squad: List of current squad Player objects
            squad_analyses: List of PlayerAnalysis for current squad
            current_budget: Current available budget

        Returns:
            OpportunityCost object or None if not possible
        """
        target_cost = target_analysis.current_price
        target_name = f"{target_analysis.player.first_name} {target_analysis.player.last_name}"

        # If budget is sufficient, no sales needed
        if current_budget >= target_cost:
            return OpportunityCost(
                target_player_name=target_name,
                target_player_id=target_analysis.player.id,
                target_cost=target_cost,
                players_to_sell=[],
                sell_proceeds=0,
                net_budget_change=current_budget - target_cost,
                net_value_score_change=target_analysis.value_score,
                net_efficiency_change=target_analysis.points_per_million,
                net_risk_change=(
                    self._get_risk_label(target_analysis.risk_metrics)
                    if target_analysis.risk_metrics
                    else None
                ),
                is_worthwhile=True,
                reason="Sufficient budget available",
                confidence=target_analysis.confidence,
            )

        # Need to sell players - find cheapest combination
        shortfall = target_cost - current_budget

        # Get sellable players (not in best 11, or if forced, worst players)
        sellable = self._get_sellable_players(squad_analyses, current_squad)

        if not sellable:
            return None  # Cannot make the purchase

        # Find minimum set of players to sell
        players_to_sell, sell_proceeds = self._find_players_to_sell(sellable, shortfall)

        if not players_to_sell:
            return None  # Cannot raise enough funds

        # Calculate net impact
        net_budget = current_budget + sell_proceeds - target_cost

        # Calculate value score impact
        sold_value_scores = sum(
            analysis.value_score
            for analysis in squad_analyses
            if analysis.player.id in [p_id for p_id, _, _ in players_to_sell]
        )
        net_value_change = target_analysis.value_score - sold_value_scores

        # Calculate efficiency impact
        sold_efficiency = sum(
            analysis.points_per_million
            for analysis in squad_analyses
            if analysis.player.id in [p_id for p_id, _, _ in players_to_sell]
        )
        net_efficiency_change = target_analysis.points_per_million - sold_efficiency

        # Calculate risk impact
        net_risk_change = None
        if target_analysis.risk_metrics:
            target_risk = target_analysis.risk_metrics.risk_category
            sold_risks = [
                analysis.risk_metrics.risk_category
                for analysis in squad_analyses
                if analysis.player.id in [p_id for p_id, _, _ in players_to_sell]
                and analysis.risk_metrics
            ]
            if sold_risks:
                net_risk_change = self._compare_risk(
                    target_risk, sold_risks[0] if sold_risks else "Medium Risk"
                )

        # Assess if worthwhile
        is_worthwhile, reason, confidence = self._assess_worthwhile(
            net_value_change=net_value_change,
            net_efficiency_change=net_efficiency_change,
            target_analysis=target_analysis,
            players_to_sell=players_to_sell,
        )

        return OpportunityCost(
            target_player_name=target_name,
            target_player_id=target_analysis.player.id,
            target_cost=target_cost,
            players_to_sell=[(name, price) for _, name, price in players_to_sell],
            sell_proceeds=sell_proceeds,
            net_budget_change=net_budget,
            net_value_score_change=net_value_change,
            net_efficiency_change=net_efficiency_change,
            net_risk_change=net_risk_change,
            is_worthwhile=is_worthwhile,
            reason=reason,
            confidence=confidence,
        )

    def _get_sellable_players(
        self,
        squad_analyses: list,
        current_squad: list,
    ) -> list[tuple[str, str, int, float]]:
        """
        Get players that can be sold

        Returns:
            List of (player_id, player_name, price, value_score) tuples
        """
        # Don't sell if it would drop below minimum squad size
        max_to_sell = max(0, len(current_squad) - self.min_squad_size)

        if max_to_sell == 0:
            return []

        # Get all players sorted by value score (worst first)
        sellable = []
        for analysis in squad_analyses:
            player_name = f"{analysis.player.first_name} {analysis.player.last_name}"
            sellable.append(
                (analysis.player.id, player_name, analysis.market_value, analysis.value_score)
            )

        # Sort by value score (ascending - worst players first)
        sellable.sort(key=lambda x: x[3])

        return sellable[:max_to_sell]

    def _find_players_to_sell(
        self, sellable: list[tuple[str, str, int, float]], shortfall: int
    ) -> tuple[list[tuple[str, str, int]], int]:
        """
        Find minimum set of players to sell to cover shortfall

        Args:
            sellable: List of (player_id, name, price, value_score)
            shortfall: Amount needed

        Returns:
            (players_to_sell, total_proceeds) where players_to_sell is [(id, name, price)]
        """
        # Greedy algorithm: sell highest-priced players first (minimize number sold)
        # But prefer lower value score players

        # Sort by price (descending) but weight by value score
        # Score: price / (value_score + 1) - higher is better to sell
        sellable_scored = [
            (player_id, name, price, value_score, price / (value_score + 1))
            for player_id, name, price, value_score in sellable
        ]
        sellable_scored.sort(key=lambda x: x[4], reverse=True)

        players_to_sell = []
        total_proceeds = 0

        for player_id, name, price, _value_score, _ in sellable_scored:
            if total_proceeds >= shortfall:
                break

            players_to_sell.append((player_id, name, price))
            total_proceeds += price

        if total_proceeds < shortfall:
            return [], 0  # Cannot raise enough

        return players_to_sell, total_proceeds

    def _assess_worthwhile(
        self,
        net_value_change: float,
        net_efficiency_change: float,
        target_analysis,
        players_to_sell: list[tuple[str, str, int]],
    ) -> tuple[bool, str, float]:
        """
        Assess if the trade is worthwhile

        Returns:
            (is_worthwhile, reason, confidence)
        """
        # Strong upgrade
        if net_value_change > 15 and net_efficiency_change >= 0:
            return True, "Strong upgrade", 0.9

        # Good upgrade
        if net_value_change > 10 and net_efficiency_change >= -1:
            return True, "Good upgrade", 0.8

        # Moderate upgrade
        if net_value_change > 5 and net_efficiency_change >= 0:
            return True, "Moderate upgrade", 0.7

        # Efficiency gain
        if net_efficiency_change > 5 and net_value_change >= 0:
            return True, "Efficiency gain", 0.75

        # Neutral or slight upgrade
        if net_value_change >= 0 and net_efficiency_change >= 0:
            return True, "Slight upgrade", 0.6

        # Too many players to sell
        if len(players_to_sell) > 2:
            return False, "Must sell too many players", 0.4

        # Downgrade
        if net_value_change < 0:
            return False, f"Downgrade ({net_value_change:+.1f} value score)", 0.3

        # Efficiency loss
        if net_efficiency_change < -2:
            return False, f"Efficiency loss ({net_efficiency_change:+.1f} pts/M)", 0.3

        # Default: marginal
        return False, "Marginal benefit", 0.5

    def _get_risk_label(self, risk_metrics) -> str:
        """Get risk category label"""
        if not risk_metrics:
            return "Unknown"
        return risk_metrics.risk_category

    def _compare_risk(self, target_risk: str, sold_risk: str) -> str:
        """Compare risk levels"""
        risk_levels = {
            "Low Risk": 1,
            "Medium Risk": 2,
            "High Risk": 3,
            "Very High Risk": 4,
        }

        target_level = risk_levels.get(target_risk, 2)
        sold_level = risk_levels.get(sold_risk, 2)

        if target_level > sold_level:
            return f"Higher risk ({target_risk} vs {sold_risk})"
        elif target_level < sold_level:
            return f"Lower risk ({target_risk} vs {sold_risk})"
        else:
            return f"Similar risk ({target_risk})"

    def display_opportunity_cost(self, analysis, cost: OpportunityCost):  # PlayerAnalysis
        """Display opportunity cost analysis as a panel"""

        # Build panel content
        lines = []
        lines.append(f"[bold cyan]💡 Opportunity Cost: {cost.target_player_name}[/bold cyan]")
        lines.append(f"[yellow]Price: €{cost.target_cost:,}[/yellow]")
        lines.append("")

        # What must be sold
        if cost.players_to_sell:
            lines.append("[bold]Must sell:[/bold]")
            for name, price in cost.players_to_sell:
                lines.append(f"  • {name} (€{price:,})")
            lines.append(f"[dim]Total proceeds: €{cost.sell_proceeds:,}[/dim]")
        else:
            lines.append("[green]✓ Sufficient budget - no sales needed[/green]")

        lines.append("")

        # Budget impact
        budget_color = "green" if cost.net_budget_change >= 0 else "red"
        lines.append(
            f"[bold]Budget after trade:[/bold] [{budget_color}]€{cost.net_budget_change:,}[/{budget_color}]"
        )
        lines.append("")

        # Net impact
        lines.append("[bold]Net Impact:[/bold]")

        # Value score
        value_color = (
            "green"
            if cost.net_value_score_change > 5
            else "yellow" if cost.net_value_score_change >= 0 else "red"
        )
        value_symbol = "✓" if cost.net_value_score_change >= 0 else "✗"
        lines.append(
            f"  Value Score: [{value_color}]{cost.net_value_score_change:+.1f} {value_symbol}[/{value_color}]"
        )

        # Efficiency
        eff_color = (
            "green"
            if cost.net_efficiency_change > 2
            else "yellow" if cost.net_efficiency_change >= 0 else "red"
        )
        eff_symbol = "✓" if cost.net_efficiency_change >= 0 else "✗"
        lines.append(
            f"  Efficiency: [{eff_color}]{cost.net_efficiency_change:+.1f} pts/M {eff_symbol}[/{eff_color}]"
        )

        # Risk
        if cost.net_risk_change:
            lines.append(f"  Risk: {cost.net_risk_change}")

        lines.append("")

        # Assessment
        worthwhile_color = "green" if cost.is_worthwhile else "red"
        worthwhile_symbol = "YES ✓" if cost.is_worthwhile else "NO ✗"
        lines.append(
            f"[bold]Worth it?[/bold] [{worthwhile_color}]{worthwhile_symbol}[/{worthwhile_color}]"
        )
        lines.append(f"[dim]{cost.reason} (confidence: {cost.confidence*100:.0f}%)[/dim]")

        # Create panel
        panel = Panel(
            "\n".join(lines),
            border_style="cyan" if cost.is_worthwhile else "yellow",
            padding=(1, 2),
        )

        console.print(panel)
