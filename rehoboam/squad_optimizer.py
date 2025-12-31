"""Squad optimization - Ensure best 11 lineup and positive budget by gameday"""

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from .formation import select_best_eleven

console = Console()


@dataclass
class SquadOptimization:
    """Result of squad optimization analysis"""

    best_eleven: list  # Best 11 players to keep
    bench_players: list  # Players #12-15
    players_to_sell: list  # Players recommended to sell
    current_budget: int
    projected_budget: int  # After recommended sales
    total_squad_value: int
    best_eleven_value: int
    bench_value: int
    is_gameday_ready: bool  # True if budget will be positive by gameday
    days_until_gameday: int | None
    recommendations: list[str]  # List of actionable recommendations


class SquadOptimizer:
    """Optimize squad composition and budget management"""

    def __init__(self, min_squad_size: int = 11, max_squad_size: int = 15):
        """
        Args:
            min_squad_size: Minimum players required (default: 11)
            max_squad_size: Maximum players allowed (default: 15)
        """
        self.min_squad_size = min_squad_size
        self.max_squad_size = max_squad_size

    def optimize_squad(
        self,
        squad: list,
        player_values: dict[str, float],
        current_budget: int,
        days_until_gameday: int | None = None,
    ) -> SquadOptimization:
        """
        Analyze squad and recommend optimal composition

        Args:
            squad: Current squad
            player_values: Dict mapping player.id -> value_score
            current_budget: Current budget (can be negative)
            days_until_gameday: Days until next gameday (None if unknown)

        Returns:
            SquadOptimization with recommendations
        """
        # Select best 11 using existing formation logic
        best_eleven = select_best_eleven(squad, player_values)
        best_eleven_ids = {p.id for p in best_eleven}

        # Identify bench players (sorted by value score, worst first)
        bench_players = [p for p in squad if p.id not in best_eleven_ids]
        bench_players_sorted = sorted(
            bench_players, key=lambda p: player_values.get(p.id, 0), reverse=False
        )

        # Calculate values
        best_eleven_value = sum(p.market_value for p in best_eleven)
        bench_value = sum(p.market_value for p in bench_players)
        total_squad_value = best_eleven_value + bench_value

        # Determine who to sell based on budget constraints
        players_to_sell = []
        projected_budget = current_budget
        recommendations = []

        # CRITICAL: If budget is negative, we MUST sell to be positive by gameday
        if current_budget < 0:
            # Need to sell enough to get positive
            debt = abs(current_budget)

            # PRIORITY 1: Sell surplus GKs first (if any)
            position_counts = {}
            for player in squad:
                pos = player.position
                if pos not in position_counts:
                    position_counts[pos] = []
                position_counts[pos].append(player)

            surplus_gks = []
            if "Goalkeeper" in position_counts and len(position_counts["Goalkeeper"]) > 1:
                gks = position_counts["Goalkeeper"]
                gks_sorted = sorted(gks, key=lambda p: player_values.get(p.id, 0), reverse=True)
                surplus_gks = [gk for gk in gks_sorted[1:] if gk.id not in best_eleven_ids]

            # Sell surplus GKs first
            for gk in surplus_gks:
                players_to_sell.append(gk)
                projected_budget += gk.market_value
                if projected_budget >= 500_000:
                    break

            # PRIORITY 2: Sell worst bench players (excluding already selected surplus GKs)
            if projected_budget < 500_000:
                for player in bench_players_sorted:
                    if player not in players_to_sell:
                        players_to_sell.append(player)
                        projected_budget += player.market_value

                        # Stop when we're positive (with small buffer)
                        if projected_budget >= 500_000:  # €500K buffer
                            break

            if projected_budget < 0:
                # Still negative after selling all bench players!
                recommendations.append(
                    f"⚠️ CRITICAL: Need €{abs(projected_budget):,} more - may need to sell from starting 11!"
                )
            else:
                sell_msg = f"Sell {len(players_to_sell)} bench player(s) to clear €{debt:,} debt"
                if surplus_gks and any(gk in players_to_sell for gk in surplus_gks):
                    sell_msg += " (includes surplus GK)"
                recommendations.append(sell_msg)

        # If budget is positive, evaluate if we should keep bench players
        elif len(bench_players) > 0:
            # PRIORITY 1: Check for redundant positions (e.g., 2 GKs when only 1 needed)
            # Count players by position
            position_counts = {}
            for player in squad:
                pos = player.position
                if pos not in position_counts:
                    position_counts[pos] = []
                position_counts[pos].append(player)

            # Identify surplus players in each position
            # Only need 1 GK in starting 11, so if we have 2+ GKs, the extras are pure bench
            surplus_gks = []
            if "Goalkeeper" in position_counts and len(position_counts["Goalkeeper"]) > 1:
                # Sort GKs by value score, keep the best one
                gks = position_counts["Goalkeeper"]
                gks_sorted = sorted(gks, key=lambda p: player_values.get(p.id, 0), reverse=True)
                # All GKs except the best one are surplus
                surplus_gks = [gk for gk in gks_sorted[1:] if gk.id not in best_eleven_ids]

            # Add surplus GKs to sell list
            for gk in surplus_gks:
                players_to_sell.append(gk)
                projected_budget += gk.market_value
                value_score = player_values.get(gk.id, 0)
                recommendations.append(
                    f"💡 Surplus GK: Sell {gk.first_name} {gk.last_name} (value: {value_score:.0f}) for €{gk.market_value:,} to free budget"
                )

            # PRIORITY 2: Check for weak bench players if budget is tight
            gameday_approaching = days_until_gameday is not None and days_until_gameday <= 2
            budget_is_tight = current_budget < 2_000_000  # Less than €2M

            if gameday_approaching and budget_is_tight:
                # Recommend selling weak bench players (not already in sell list)
                for player in bench_players_sorted:
                    if player not in players_to_sell:
                        value_score = player_values.get(player.id, 0)
                        if value_score < 30:  # Very weak player
                            players_to_sell.append(player)
                            projected_budget += player.market_value

                if len(players_to_sell) > len(surplus_gks):  # If we added more beyond surplus GKs
                    recommendations.append(
                        f"Gameday in {days_until_gameday} days: Sell {len(players_to_sell) - len(surplus_gks)} weak bench player(s) for extra budget"
                    )
            elif not surplus_gks:
                # Budget is healthy and no surplus players - keep bench
                recommendations.append(
                    f"Squad depth OK: Keeping {len(bench_players)} bench player(s) (budget: €{current_budget:,})"
                )

        # Check if we'll be gameday ready
        is_gameday_ready = projected_budget >= 0

        if not is_gameday_ready:
            recommendations.insert(
                0, f"❌ NOT READY: Budget will be -€{abs(projected_budget):,} by gameday"
            )
        elif days_until_gameday is not None and days_until_gameday <= 3:
            recommendations.insert(
                0,
                f"✅ READY: Budget will be €{projected_budget:,} by gameday ({days_until_gameday}d)",
            )

        return SquadOptimization(
            best_eleven=best_eleven,
            bench_players=bench_players_sorted,
            players_to_sell=players_to_sell,
            current_budget=current_budget,
            projected_budget=projected_budget,
            total_squad_value=total_squad_value,
            best_eleven_value=best_eleven_value,
            bench_value=bench_value,
            is_gameday_ready=is_gameday_ready,
            days_until_gameday=days_until_gameday,
            recommendations=recommendations,
        )

    def display_optimization(
        self, optimization: SquadOptimization, player_values: dict[str, float]
    ):
        """Display squad optimization results"""

        console.print("\n[bold cyan]📋 Squad Optimization[/bold cyan]")

        # Show recommendations first
        console.print("\n[bold]Recommendations:[/bold]")
        for rec in optimization.recommendations:
            if "❌" in rec or "CRITICAL" in rec:
                console.print(f"[red]{rec}[/red]")
            elif "✅" in rec:
                console.print(f"[green]{rec}[/green]")
            elif "⚠️" in rec:
                console.print(f"[yellow]{rec}[/yellow]")
            else:
                console.print(f"[dim]{rec}[/dim]")

        # Show budget summary
        console.print("\n[bold]Budget Summary:[/bold]")
        budget_color = "green" if optimization.current_budget >= 0 else "red"
        console.print(
            f"  Current Budget: [{budget_color}]€{optimization.current_budget:,}[/{budget_color}]"
        )

        if optimization.players_to_sell:
            proceeds = sum(p.market_value for p in optimization.players_to_sell)
            projected_color = "green" if optimization.projected_budget >= 0 else "red"
            console.print(
                f"  After Sales: [{projected_color}]€{optimization.projected_budget:,}[/{projected_color}] (+€{proceeds:,})"
            )

        # Show squad value breakdown
        console.print("\n[bold]Squad Value:[/bold]")
        console.print(f"  Best 11: €{optimization.best_eleven_value:,}")
        console.print(f"  Bench ({len(optimization.bench_players)}): €{optimization.bench_value:,}")
        console.print(
            f"  Total ({len(optimization.best_eleven) + len(optimization.bench_players)}): €{optimization.total_squad_value:,}"
        )

        # Show best 11 lineup
        console.print("\n[bold green]🌟 Your Best 11 Starting Lineup[/bold green]")
        lineup_table = Table(show_header=True, header_style="bold magenta")
        lineup_table.add_column("Player", style="cyan")
        lineup_table.add_column("Position", style="blue")
        lineup_table.add_column("Value Score", justify="right", style="green")
        lineup_table.add_column("Avg Points", justify="right", style="yellow")
        lineup_table.add_column("Market Value", justify="right", style="dim")

        # Sort by position, then by value score
        position_order = {"Goalkeeper": 0, "Defender": 1, "Midfielder": 2, "Forward": 3}
        best_eleven_sorted = sorted(
            optimization.best_eleven,
            key=lambda p: (
                position_order.get(p.position, 99),
                -player_values.get(p.id, 0),  # Negative for descending order
            ),
        )

        for player in best_eleven_sorted:
            value_score = player_values.get(player.id, 0)
            score_color = "green" if value_score >= 60 else "yellow" if value_score >= 40 else "red"

            lineup_table.add_row(
                f"{player.first_name} {player.last_name}",
                player.position,
                f"[{score_color}]{value_score:.1f}[/{score_color}]",
                f"{player.average_points:.1f}",
                f"€{player.market_value:,}",
            )

        console.print(lineup_table)

        # Show bench players if any
        if optimization.bench_players:
            console.print(
                f"\n[bold yellow]Bench Players ({len(optimization.bench_players)})[/bold yellow]"
            )
            bench_table = Table(show_header=True, header_style="bold magenta")
            bench_table.add_column("Player", style="cyan")
            bench_table.add_column("Position", style="blue")
            bench_table.add_column("Value Score", justify="right", style="yellow")
            bench_table.add_column("Avg Points", justify="right", style="dim")
            bench_table.add_column("Market Value", justify="right", style="green")
            bench_table.add_column("Recommendation", justify="center")

            # Sort bench by position, then by value score
            position_order = {"Goalkeeper": 0, "Defender": 1, "Midfielder": 2, "Forward": 3}
            bench_sorted = sorted(
                optimization.bench_players,
                key=lambda p: (position_order.get(p.position, 99), -player_values.get(p.id, 0)),
            )

            for player in bench_sorted:
                value_score = player_values.get(player.id, 0)
                should_sell = player in optimization.players_to_sell

                rec_text = "[red]SELL[/red]" if should_sell else "[dim]KEEP[/dim]"

                bench_table.add_row(
                    f"{player.first_name} {player.last_name}",
                    player.position,
                    f"{value_score:.1f}",
                    f"{player.average_points:.1f}",
                    f"€{player.market_value:,}",
                    rec_text,
                )

            console.print(bench_table)

        # Show sell recommendations summary
        if optimization.players_to_sell:
            console.print(
                f"\n[bold red]Players to Sell ({len(optimization.players_to_sell)})[/bold red]"
            )
            total_proceeds = sum(p.market_value for p in optimization.players_to_sell)
            console.print(f"  Total Proceeds: [green]€{total_proceeds:,}[/green]")
            console.print(f"  New Budget: [green]€{optimization.projected_budget:,}[/green]")

            for player in optimization.players_to_sell:
                console.print(
                    f"    • {player.first_name} {player.last_name} ({player.position}) - €{player.market_value:,}"
                )

    def execute_sell_recommendations(
        self, optimization: SquadOptimization, api, league, dry_run: bool = True
    ) -> dict:
        """
        Execute recommended sales

        Args:
            optimization: SquadOptimization result
            api: KickbaseAPI instance
            league: League object
            dry_run: If True, simulate sales without executing

        Returns:
            dict with results
        """
        results = {"sold": [], "failed": []}

        if not optimization.players_to_sell:
            console.print("[yellow]No players to sell[/yellow]")
            return results

        console.print(
            f"\n[cyan]Executing {len(optimization.players_to_sell)} recommended sales...[/cyan]"
        )

        for player in optimization.players_to_sell:
            sell_price = player.market_value

            if dry_run:
                console.print(
                    f"[blue][DRY RUN] Would sell {player.first_name} {player.last_name} for €{sell_price:,}[/blue]"
                )
                results["sold"].append(player)
            else:
                try:
                    api.sell_player(league=league, player=player, price=sell_price)
                    console.print(
                        f"[green]✓ Listed {player.first_name} {player.last_name} for €{sell_price:,}[/green]"
                    )
                    results["sold"].append(player)
                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to sell {player.first_name} {player.last_name}: {e}[/red]"
                    )
                    results["failed"].append(player)

        total_proceeds = sum(p.market_value for p in results["sold"])
        console.print(f"\n[green]Total proceeds: €{total_proceeds:,}[/green]")

        return results
