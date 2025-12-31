#!/usr/bin/env python3
"""Compare two players side-by-side to understand bot decision logic"""

import sys

from rich.console import Console
from rich.table import Table

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings
from rehoboam.value_calculator import PlayerValue

console = Console()


def compare_players(player1_name: str, player2_name: str):
    """Compare two players and show why bot prefers one over the other"""

    # Initialize API
    settings = get_settings()
    api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
    api.login()

    leagues = api.get_leagues()
    if not leagues:
        console.print("[red]No leagues found[/red]")
        return

    league = leagues[0]  # Use first league

    # Get squad
    squad = api.get_squad(league)

    # Find players
    player1 = None
    player2 = None

    for player in squad:
        full_name = f"{player.first_name} {player.last_name}".lower()
        if player1_name.lower() in full_name:
            player1 = player
        if player2_name.lower() in full_name:
            player2 = player

    if not player1:
        console.print(f"[red]Could not find player: {player1_name}[/red]")
        return

    if not player2:
        console.print(f"[red]Could not find player: {player2_name}[/red]")
        return

    # Calculate value scores with detailed breakdown
    console.print("\n[bold cyan]Comparing Players[/bold cyan]")
    console.print(
        f"[cyan]{player1.first_name} {player1.last_name}[/cyan] vs [cyan]{player2.first_name} {player2.last_name}[/cyan]\n"
    )

    # Get performance data
    try:
        perf1 = api.client.get_player_performance(league.id, player1.id)
    except Exception:
        perf1 = None

    try:
        perf2 = api.client.get_player_performance(league.id, player2.id)
    except Exception:
        perf2 = None

    value1 = PlayerValue.calculate(player1, performance_data=perf1)
    value2 = PlayerValue.calculate(player2, performance_data=perf2)

    # Create comparison table
    table = Table(title="Player Comparison", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column(f"{player1.first_name} {player1.last_name}", justify="right", style="yellow")
    table.add_column(f"{player2.first_name} {player2.last_name}", justify="right", style="yellow")
    table.add_column("Difference", justify="right", style="green")

    # Basic stats
    table.add_row(
        "Position",
        player1.position,
        player2.position,
        "✓" if player1.position == player2.position else "Different",
    )

    table.add_row(
        "Market Value",
        f"€{player1.market_value:,}",
        f"€{player2.market_value:,}",
        f"€{abs(player1.market_value - player2.market_value):,}",
    )

    table.add_row(
        "Season Points",
        str(player1.points),
        str(player2.points),
        f"{abs(player1.points - player2.points):+.0f}",
    )

    table.add_row(
        "Average Points",
        f"{player1.average_points:.1f}",
        f"{player2.average_points:.1f}",
        f"{abs(player1.average_points - player2.average_points):+.1f}",
    )

    # Value metrics
    table.add_row(
        "[bold]Points per €M[/bold]",
        f"{value1.points_per_million:.1f}",
        f"{value2.points_per_million:.1f}",
        f"{abs(value1.points_per_million - value2.points_per_million):+.1f}",
    )

    # Sample size
    if value1.games_played and value2.games_played:
        table.add_row(
            "Games Played",
            str(value1.games_played),
            str(value2.games_played),
            f"{abs(value1.games_played - value2.games_played)}",
        )

        table.add_row(
            "Sample Confidence",
            f"{value1.sample_size_confidence * 100:.0f}%",
            f"{value2.sample_size_confidence * 100:.0f}%",
            f"{abs(value1.sample_size_confidence - value2.sample_size_confidence) * 100:+.0f}%",
        )

    # Trends
    if value1.trend_direction and value2.trend_direction:
        table.add_row(
            "Market Trend",
            f"{value1.trend_direction} ({value1.trend_pct:+.1f}%)",
            f"{value2.trend_direction} ({value2.trend_pct:+.1f}%)",
            "-",
        )

    # Final scores
    table.add_row(
        "[bold]VALUE SCORE[/bold]",
        f"[bold]{value1.value_score:.1f}/100[/bold]",
        f"[bold]{value2.value_score:.1f}/100[/bold]",
        f"[bold green]{abs(value1.value_score - value2.value_score):+.1f}[/bold green]",
    )

    console.print(table)

    # Explain the difference
    console.print("\n[bold]Analysis:[/bold]")

    diff = value1.value_score - value2.value_score
    winner = (
        f"{player1.first_name} {player1.last_name}"
        if diff > 0
        else f"{player2.first_name} {player2.last_name}"
    )

    if abs(diff) < 5:
        console.print(f"[yellow]Very close! Only {abs(diff):.1f} points difference.[/yellow]")
        console.print("[yellow]Could be affected by recent form or upcoming schedule.[/yellow]")
    elif abs(diff) < 15:
        console.print(
            f"[cyan]Moderate difference: {abs(diff):.1f} points in favor of {winner}[/cyan]"
        )
    else:
        console.print(f"[green]Clear winner: {winner} by {abs(diff):.1f} points[/green]")

    # Key differentiators
    console.print("\n[bold]Key Differences:[/bold]")

    if value1.games_played and value2.games_played:
        if abs(value1.games_played - value2.games_played) >= 3:
            console.print(f"⚠️  Sample size: {value1.games_played} vs {value2.games_played} games")

    if abs(value1.points_per_million - value2.points_per_million) > 2:
        console.print(
            f"💰 Efficiency: {value1.points_per_million:.1f} vs {value2.points_per_million:.1f} pts/M€"
        )

    if abs(player1.average_points - player2.average_points) > 5:
        console.print(
            f"📊 Historical: {player1.average_points:.1f} vs {player2.average_points:.1f} avg pts"
        )

    if value1.trend_direction != value2.trend_direction:
        console.print(f"📈 Trend: {value1.trend_direction} vs {value2.trend_direction}")

    console.print("\n[dim]Note: This doesn't include SOS (Strength of Schedule) adjustments.[/dim]")
    console.print("[dim]Run 'rehoboam analyze' to see full analysis with matchups.[/dim]\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        console.print(
            "[yellow]Usage: python compare_players.py <player1_name> <player2_name>[/yellow]"
        )
        console.print("[yellow]Example: python compare_players.py Lienhart Hranac[/yellow]")
        sys.exit(1)

    compare_players(sys.argv[1], sys.argv[2])
