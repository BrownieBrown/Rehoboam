"""Analyze squad to determine which 3 players to keep when selling all others"""

from rich.console import Console
from rich.table import Table

from rehoboam.analyzer import MarketAnalyzer
from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings
from rehoboam.value_calculator import PlayerValue

console = Console()


def main():
    # Initialize
    settings = get_settings()
    api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
    api.login()

    # Get league
    leagues = api.get_leagues()
    league = leagues[0]  # First league

    console.print(f"\n[bold cyan]🏆 {league.name}[/bold cyan]")
    console.print("\n[bold]Analyzing your squad to determine top 3 players to keep...[/bold]\n")

    # Get squad
    players = api.get_squad(league)

    # Analyze each player
    analyzer = MarketAnalyzer(
        min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
        min_sell_profit_pct=settings.min_sell_profit_pct,
        max_loss_pct=settings.max_loss_pct,
    )

    # Calculate metrics for each player
    player_evaluations = []

    for player in players:
        # Calculate player value
        player_value = PlayerValue.calculate(player)

        # Create evaluation dict
        evaluation = {
            "name": f"{player.first_name} {player.last_name}",
            "position": player.position,
            "market_value": player.market_value,
            "points": player.points,
            "avg_points": player.average_points,
            "value_score": player_value.value_score,
            "points_per_million": player_value.points_per_million,
            "avg_points_per_million": player_value.avg_points_per_million,
            "status": getattr(player, "status", 0),  # 0 = healthy, 1 = injured
            "player_obj": player,
        }

        # Calculate a composite "keep score" based on multiple factors
        keep_score = 0.0

        # Factor 1: Value score (0-100) - weight 30%
        keep_score += player_value.value_score * 0.3

        # Factor 2: Points per million (efficiency) - weight 25%
        # Normalize to 0-100 scale (assume 10 pts/M is excellent)
        efficiency_score = min((player_value.points_per_million / 10.0) * 100, 100)
        keep_score += efficiency_score * 0.25

        # Factor 3: Total points (absolute performance) - weight 20%
        # Normalize to 0-100 scale (assume 100+ pts is excellent)
        points_score = min((player.points / 100.0) * 100, 100)
        keep_score += points_score * 0.20

        # Factor 4: Average points (consistency) - weight 15%
        # Normalize to 0-100 scale (assume 8+ avg is excellent)
        avg_score = min((player.average_points / 8.0) * 100, 100)
        keep_score += avg_score * 0.15

        # Factor 5: Market value (asset value) - weight 10%
        # Normalize to 0-100 scale (assume 20M+ is high value)
        value_asset_score = min((player.market_value / 20_000_000) * 100, 100)
        keep_score += value_asset_score * 0.10

        # Penalty for injured players
        if evaluation["status"] != 0:
            keep_score *= 0.5  # 50% penalty for injury

        evaluation["keep_score"] = keep_score
        player_evaluations.append(evaluation)

    # Sort by keep_score descending
    player_evaluations.sort(key=lambda x: x["keep_score"], reverse=True)

    # Display all players with scores
    console.print("\n[bold]Complete Squad Ranking (by Keep Score):[/bold]\n")

    table = Table(show_header=True)
    table.add_column("Rank", style="cyan", width=6)
    table.add_column("Player", style="white", width=20)
    table.add_column("Pos", width=6)
    table.add_column("Keep Score", justify="right", width=12)
    table.add_column("Value Score", justify="right", width=12)
    table.add_column("Pts/M€", justify="right", width=10)
    table.add_column("Points", justify="right", width=10)
    table.add_column("Avg Pts", justify="right", width=10)
    table.add_column("Market Value", justify="right", width=15)
    table.add_column("Status", width=10)

    for i, eval_data in enumerate(player_evaluations, 1):
        status = "Healthy" if eval_data["status"] == 0 else "⚠️ Injured"

        # Highlight top 3
        style = "bold green" if i <= 3 else ""

        table.add_row(
            f"#{i}",
            eval_data["name"],
            eval_data["position"],
            f"{eval_data['keep_score']:.1f}",
            f"{eval_data['value_score']:.1f}",
            f"{eval_data['points_per_million']:.1f}",
            f"{eval_data['points']}",
            f"{eval_data['avg_points']:.1f}",
            f"€{eval_data['market_value']:,}",
            status,
            style=style,
        )

    console.print(table)

    # Show recommendation
    console.print("\n[bold green]═══════════════════════════════════════════════════[/bold green]")
    console.print("[bold green]TOP 3 PLAYERS TO KEEP:[/bold green]")
    console.print("[bold green]═══════════════════════════════════════════════════[/bold green]\n")

    for i in range(min(3, len(player_evaluations))):
        eval_data = player_evaluations[i]
        console.print(
            f"[bold green]#{i+1}. {eval_data['name']}[/bold green] ({eval_data['position']})"
        )
        console.print(f"   Keep Score: {eval_data['keep_score']:.1f}/100")
        console.print(f"   Value Score: {eval_data['value_score']:.1f}/100")
        console.print(f"   Efficiency: {eval_data['points_per_million']:.1f} pts/M€")
        console.print(
            f"   Performance: {eval_data['points']} pts ({eval_data['avg_points']:.1f} avg)"
        )
        console.print(f"   Market Value: €{eval_data['market_value']:,}")
        console.print()

    # Show players to sell
    console.print("\n[bold red]PLAYERS TO SELL (All Others):[/bold red]\n")
    total_sale_value = 0
    for i in range(3, len(player_evaluations)):
        eval_data = player_evaluations[i]
        total_sale_value += eval_data["market_value"]
        console.print(
            f"  • {eval_data['name']} ({eval_data['position']}) - €{eval_data['market_value']:,}"
        )

    console.print(f"\n[bold]Total expected from sales: €{total_sale_value:,}[/bold]")
    console.print(f"[bold]Number of players to sell: {len(player_evaluations) - 3}[/bold]\n")


if __name__ == "__main__":
    main()
