#!/usr/bin/env python3
"""Show top 10 trade recommendations with detailed scoring"""

from rich.console import Console
from rich.table import Table

from rehoboam.api import KickbaseAPI
from rehoboam.config import get_settings
from rehoboam.trader import Trader

console = Console()


def calculate_trade_score(trade):
    """Same logic as trade_optimizer.py:136-156"""
    score = trade.improvement_points + (trade.improvement_value / 10)

    # Starter quality bonus
    if trade.players_in:
        avg_quality = sum(p.average_points for p in trade.players_in) / len(trade.players_in)
        if avg_quality > 50:
            score += 2.0
        elif avg_quality > 40:
            score += 1.5
        elif avg_quality > 30:
            score += 1.0
        elif avg_quality > 20:
            score += 0.5

    return score


def main():
    console.print("[dim]Loading config and connecting...[/dim]")
    settings = get_settings()
    api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
    api.login()

    leagues = api.get_leagues()
    league = leagues[0]

    console.print("\n[bold cyan]Finding trade opportunities...[/bold cyan]")
    trader = Trader(api, settings)
    trades = trader.find_trade_opportunities(league)

    if not trades:
        console.print("[red]No trades found[/red]")
        return

    console.print(f"[green]Found {len(trades)} total trades[/green]\n")

    # Show current starting 11
    my_squad = api.get_squad(league)
    market = api.get_market(league)
    kickbase_market = [p for p in market if p.is_kickbase_seller()]
    player_trends = trader._fetch_player_trends(list(kickbase_market) + list(my_squad), limit=100)

    # Calculate player values (same way trader does it)
    player_values = {}
    for player in list(my_squad) + kickbase_market:
        try:
            from rehoboam.value_calculator import PlayerValue

            trend_data = player_trends.get(player.id)
            value = PlayerValue.calculate(player, trend_data=trend_data)
            player_values[player.id] = value.value_score
        except Exception:
            player_values[player.id] = 0

    from rehoboam.formation import select_best_eleven

    current_eleven = select_best_eleven(my_squad, player_values)

    console.print("[bold cyan]Your Current Starting XI:[/bold cyan]")
    table = Table()
    table.add_column("Pos", style="cyan")
    table.add_column("Player", style="white")
    table.add_column("Avg Pts", justify="right")
    table.add_column("Value Score", justify="right")

    for p in sorted(current_eleven, key=lambda x: x.position):
        value_score = player_values.get(p.id, 0)
        table.add_row(
            p.position[:3],
            f"{p.first_name} {p.last_name}",
            f"{p.average_points:.1f}",
            f"{value_score:.1f}",
        )

    console.print(table)
    total_pts = sum(p.average_points for p in current_eleven)
    console.print(f"[dim]Total: {total_pts:.1f} pts/week[/dim]\n")

    # Show top 10 trades with scoring breakdown
    console.print("[bold magenta]Top 10 Trade Recommendations:[/bold magenta]\n")

    for idx, trade in enumerate(trades[:10], 1):
        score = calculate_trade_score(trade)

        # Players in
        players_in_str = ", ".join(
            [f"{p.first_name} {p.last_name} ({p.average_points:.1f} pts)" for p in trade.players_in]
        )

        # Players out
        if trade.players_out:
            players_out_str = ", ".join(
                [
                    f"{p.first_name} {p.last_name} ({p.average_points:.1f} pts)"
                    for p in trade.players_out
                ]
            )
        else:
            players_out_str = "None (0-for-M)"

        # Calculate components
        avg_quality = (
            sum(p.average_points for p in trade.players_in) / len(trade.players_in)
            if trade.players_in
            else 0
        )
        if avg_quality > 50:
            quality_bonus = 2.0
            quality_label = "ELITE"
        elif avg_quality > 40:
            quality_bonus = 1.5
            quality_label = "Very Good"
        elif avg_quality > 30:
            quality_bonus = 1.0
            quality_label = "Good"
        elif avg_quality > 20:
            quality_bonus = 0.5
            quality_label = "Decent"
        else:
            quality_bonus = 0.0
            quality_label = "Weak"

        console.print(f"[bold cyan]#{idx}: {trade.strategy.upper()}[/bold cyan]")
        console.print(f"  [green]BUY:[/green] {players_in_str}")
        console.print(f"  [red]SELL:[/red] {players_out_str}")
        console.print(
            f"  [yellow]Cost:[/yellow] €{trade.total_cost:,} | [yellow]Net:[/yellow] €{trade.net_cost:,}"
        )
        console.print("\n  [bold]Scoring Breakdown:[/bold]")
        console.print(f"    Points improvement: +{trade.improvement_points:.1f} pts/week")
        console.print(
            f"    Value improvement: +{trade.improvement_value:.1f} (×0.1 = +{trade.improvement_value/10:.1f})"
        )
        console.print(
            f"    Quality bonus: +{quality_bonus:.1f} ({quality_label}, {avg_quality:.1f} avg pts)"
        )
        console.print(f"    [bold cyan]TOTAL SCORE: {score:.2f}[/bold cyan]\n")

    # Check if Querfeld is in the market
    querfeld = next((p for p in kickbase_market if "Querfeld" in p.last_name), None)
    if querfeld:
        console.print("\n[bold yellow]Leopold Querfeld Analysis:[/bold yellow]")
        console.print(f"  Market Value: €{querfeld.market_value:,}")
        console.print(f"  Average Points: {querfeld.average_points:.1f}")
        console.print(f"  Value Score: {player_values.get(querfeld.id, 0):.1f}")
        console.print(
            f"  Status: {querfeld.status} ({'healthy' if querfeld.status == 0 else 'INJURED'})"
        )

        # Check if he's in starting 11
        if querfeld in current_eleven:
            console.print("  [green]✓ Already in your starting XI[/green]")
        else:
            console.print("  [yellow]✗ Not in your starting XI[/yellow]")

        # Check if he's in any recommended trade
        in_trade = False
        for idx, trade in enumerate(trades[:10], 1):
            if any(p.id == querfeld.id for p in trade.players_in):
                console.print(f"  [cyan]→ Appears in trade #{idx}[/cyan]")
                in_trade = True

        if not in_trade:
            console.print("  [red]✗ Not in top 10 trades[/red]")
            console.print("  [dim]Reason: Other combinations give higher improvement[/dim]")


if __name__ == "__main__":
    main()
