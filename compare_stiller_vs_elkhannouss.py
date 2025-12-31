"""Detailed comparison between Stiller and El Khannouss"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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
    league = leagues[0]

    # Get squad
    players = api.get_squad(league)

    # Find both players
    stiller = None
    el_khannouss = None

    for player in players:
        name = f"{player.first_name} {player.last_name}"
        if "Stiller" in name:
            stiller = player
        elif "El Khannouss" in name:
            el_khannouss = player

    if not stiller or not el_khannouss:
        console.print("[red]Could not find both players[/red]")
        return

    # Calculate values
    stiller_value = PlayerValue.calculate(stiller)
    elk_value = PlayerValue.calculate(el_khannouss)

    console.print("\n[bold cyan]🔍 STILLER vs EL KHANNOUSS - Detailed Comparison[/bold cyan]\n")

    # Create comparison table
    table = Table(show_header=True, title="Head-to-Head Metrics")
    table.add_column("Metric", style="cyan", width=25)
    table.add_column("Stiller", justify="right", width=20)
    table.add_column("El Khannouss", justify="right", width=20)
    table.add_column("Winner", style="bold", width=15)

    def add_comparison_row(
        metric, stiller_val, elk_val, higher_better=True, format_euro=False, format_pct=False
    ):
        if format_euro:
            s_display = f"€{stiller_val:,.0f}"
            e_display = f"€{elk_val:,.0f}"
        elif format_pct:
            s_display = f"{stiller_val:+.1f}%"
            e_display = f"{elk_val:+.1f}%"
        elif isinstance(stiller_val, float):
            s_display = f"{stiller_val:.1f}"
            e_display = f"{elk_val:.1f}"
        else:
            s_display = str(stiller_val)
            e_display = str(elk_val)

        if higher_better:
            winner = (
                "Stiller ✓"
                if stiller_val > elk_val
                else "El Khannouss ✓" if elk_val > stiller_val else "Tie"
            )
        else:
            winner = (
                "Stiller ✓"
                if stiller_val < elk_val
                else "El Khannouss ✓" if elk_val < stiller_val else "Tie"
            )

        table.add_row(metric, s_display, e_display, winner)

    # Add all metrics
    add_comparison_row(
        "Market Value", stiller.market_value, el_khannouss.market_value, True, format_euro=True
    )
    add_comparison_row("Total Points", stiller.points, el_khannouss.points, True)
    add_comparison_row("Average Points", stiller.average_points, el_khannouss.average_points, True)
    add_comparison_row(
        "Points per Million €", stiller_value.points_per_million, elk_value.points_per_million, True
    )
    add_comparison_row(
        "Avg Pts per Million €",
        stiller_value.avg_points_per_million,
        elk_value.avg_points_per_million,
        True,
    )
    add_comparison_row("Value Score", stiller_value.value_score, elk_value.value_score, True)

    console.print(table)

    # Financial analysis
    console.print("\n[bold]💰 Financial Analysis:[/bold]\n")

    stiller_cost = stiller.market_value
    elk_cost = el_khannouss.market_value
    savings = stiller_cost - elk_cost

    console.print(f"  Stiller Market Value:      €{stiller_cost:,}")
    console.print(f"  El Khannouss Market Value: €{elk_cost:,}")
    console.print(f"  [bold green]Savings if you keep El Khannouss: €{savings:,}[/bold green]")
    console.print(f"  That's {(savings/stiller_cost)*100:.1f}% cheaper!\n")

    # Performance efficiency
    console.print("[bold]📊 Performance Efficiency:[/bold]\n")

    stiller_efficiency = stiller_value.points_per_million
    elk_efficiency = elk_value.points_per_million
    efficiency_diff = elk_efficiency - stiller_efficiency

    console.print(f"  Stiller:      {stiller_efficiency:.1f} pts/M€")
    console.print(f"  El Khannouss: {elk_efficiency:.1f} pts/M€")
    console.print(
        f"  [bold green]El Khannouss is {efficiency_diff:.1f} pts/M€ more efficient ({(efficiency_diff/stiller_efficiency)*100:.1f}% better)[/bold green]\n"
    )

    # Absolute performance
    console.print("[bold]🎯 Absolute Performance:[/bold]\n")

    points_diff = stiller.points - el_khannouss.points
    avg_diff = stiller.average_points - el_khannouss.average_points

    console.print(f"  Stiller:      {stiller.points} total pts ({stiller.average_points:.1f} avg)")
    console.print(
        f"  El Khannouss: {el_khannouss.points} total pts ({el_khannouss.average_points:.1f} avg)"
    )
    if points_diff > 0:
        console.print(f"  [bold yellow]Stiller has {points_diff} more total points[/bold yellow]")
    else:
        console.print(
            f"  [bold yellow]El Khannouss has {abs(points_diff)} more total points[/bold yellow]"
        )

    if avg_diff > 0:
        console.print(f"  [bold yellow]Stiller has {avg_diff:.1f} higher average[/bold yellow]\n")
    else:
        console.print(
            f"  [bold yellow]El Khannouss has {abs(avg_diff):.1f} higher average[/bold yellow]\n"
        )

    # Strategic considerations
    console.print("\n[bold cyan]🤔 STRATEGIC CONSIDERATIONS:[/bold cyan]\n")

    # Case for Stiller
    stiller_case = Panel(
        f"""• Higher market value (€{stiller_cost:,}) = bigger asset
• Slightly more total points ({stiller.points} vs {el_khannouss.points})
• If you need to raise cash later, Stiller can be sold for more
• More stable/established value""",
        title="[bold green]Case for STILLER[/bold green]",
        border_style="green",
    )
    console.print(stiller_case)

    # Case for El Khannouss
    elk_case = Panel(
        f"""• [bold]WAY more efficient[/bold] ({elk_efficiency:.1f} vs {stiller_efficiency:.1f} pts/M€)
• Higher average points ({el_khannouss.average_points:.1f} vs {stiller.average_points:.1f})
• Saves you €{savings:,} to invest elsewhere
• Better value-for-money player
• With €{savings:,} saved, you could buy another strong player later""",
        title="[bold green]Case for EL KHANNOUSS[/bold green]",
        border_style="green",
    )
    console.print(elk_case)

    # Recommendation
    console.print("\n[bold yellow]💡 MY RECOMMENDATION:[/bold yellow]\n")

    recommendation = """Given that you're selling 9 players today and will have limited squad:

[bold green]→ KEEP EL KHANNOUSS[/bold green]

Why?
1. He's significantly more efficient (44% better pts/M€ ratio)
2. Higher average points (better current form)
3. Saves you €11.5M which is crucial when rebuilding with only 3 players
4. You need efficiency more than absolute value when running a skeleton squad
5. The €11.5M saved could be the difference in affording a key player later

Stiller is a great asset, but El Khannouss gives you better bang-for-buck,
which is exactly what you need when you're down to 3 players."""

    console.print(Panel(recommendation, border_style="bold yellow"))

    console.print()


if __name__ == "__main__":
    main()
