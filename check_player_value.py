#!/usr/bin/env python3
"""Quick script to check why a player has a certain value score"""

import sys

from rich.console import Console

from rehoboam.api import KickbaseAPI
from rehoboam.config import load_config
from rehoboam.trader import Trader

console = Console()


def explain_value_score(player, trend_data):
    """Show detailed breakdown of value score calculation"""

    price = getattr(player, "price", player.market_value)
    price_millions = price / 1_000_000
    points = player.points
    avg_points = player.average_points

    console.print(f"\n[bold cyan]{'=' * 60}[/bold cyan]")
    console.print(
        f"[bold cyan]{player.first_name} {player.last_name} ({player.position})[/bold cyan]"
    )
    console.print(f"[bold cyan]{'=' * 60}[/bold cyan]")

    # Basic stats
    console.print("\n[bold]Basic Stats:[/bold]")
    console.print(f"  Market Value: €{player.market_value:,}")
    console.print(f"  Price: €{price:,}")
    console.print(f"  Points (last matchday): {points}")
    console.print(f"  Average Points: {avg_points:.1f}")
    console.print(f"  Status: {player.status} ({'healthy' if player.status == 0 else 'INJURED'})")

    # Trend data
    if trend_data and trend_data.get("has_data"):
        console.print("\n[bold]Market Trends:[/bold]")
        trend = trend_data.get("trend", "unknown")
        trend_pct = trend_data.get("trend_pct", 0)
        peak_value = trend_data.get("peak_value", 0)
        current_value = trend_data.get("current_value", player.market_value)
        vs_peak_pct = ((current_value - peak_value) / peak_value) * 100 if peak_value > 0 else 0

        trend_color = "green" if trend == "rising" else "red" if trend == "falling" else "yellow"
        console.print(f"  14-day trend: [{trend_color}]{trend} ({trend_pct:+.1f}%)[/{trend_color}]")
        console.print(f"  Peak value: €{peak_value:,}")
        console.print(f"  Current vs peak: {vs_peak_pct:+.1f}%")

    # Calculate value score components
    console.print("\n[bold]Value Score Breakdown (0-100):[/bold]")

    # 1. Points efficiency (0-40)
    points_efficiency = min((points / price_millions) / 10 * 40, 40)
    console.print(f"  1. Points Efficiency: [cyan]{points_efficiency:.1f}/40[/cyan]")
    console.print(
        f"     → {points} pts / {price_millions:.1f}M€ = {points/price_millions:.1f} pts/M€"
    )
    console.print("     → Target: 10+ pts/M€ = 40 points")

    # 2. Average points (0-25)
    avg_efficiency = min(avg_points * 4, 25)
    console.print(f"  2. Average Points: [cyan]{avg_efficiency:.1f}/25[/cyan]")
    console.print(f"     → {avg_points:.1f} avg × 4 = {avg_efficiency:.1f}")
    console.print("     → Target: 70+ avg pts = 25 points")

    # 3. Affordability (0-15)
    if price_millions < 5:
        affordability = 15
    elif price_millions < 10:
        affordability = 10
    elif price_millions < 20:
        affordability = 5
    else:
        affordability = 0
    console.print(f"  3. Affordability: [cyan]{affordability}/15[/cyan]")
    console.print(
        f"     → €{price_millions:.1f}M: <€5M=15pts, €5-10M=10pts, €10-20M=5pts, >€20M=0pts"
    )

    # 4. Current form (0-20)
    if points > avg_points * 3:
        form = 20
        form_desc = "HOT STREAK! 3x avg"
    elif points > avg_points * 2:
        form = 15
        form_desc = "Great week (2x avg)"
    elif points > avg_points:
        form = 10
        form_desc = "Above average"
    elif points >= avg_points * 0.5 and points > 0:
        form = 5
        form_desc = "Reasonable"
    elif points == 0 and avg_points > 50:
        form = -15
        form_desc = "⚠️ STRONG PLAYER NOT PLAYING!"
    elif points == 0 and avg_points > 20:
        form = -10
        form_desc = "⚠️ Decent player not playing"
    elif points == 0 and avg_points > 0:
        form = -5
        form_desc = "Weak player not playing"
    else:
        form = 0
        form_desc = "No data"

    form_color = "green" if form > 0 else "red" if form < 0 else "yellow"
    console.print(f"  4. Current Form: [{form_color}]{form:+}/20[/{form_color}]")
    console.print(f"     → {form_desc}")

    # 5. Market momentum (0-15)
    momentum = 0
    momentum_desc = "No trend data"

    if trend_data and trend_data.get("has_data"):
        trend_direction = trend_data.get("trend", "unknown")
        trend_pct = trend_data.get("trend_pct", 0)
        peak_value = trend_data.get("peak_value", 0)
        current_value = trend_data.get("current_value", player.market_value)
        vs_peak_pct = ((current_value - peak_value) / peak_value) * 100 if peak_value > 0 else None

        if trend_direction == "rising":
            if trend_pct > 15:
                momentum = 15
                momentum_desc = f"Strong rise ({trend_pct:+.1f}%)"
            elif trend_pct > 5:
                momentum = 10
                momentum_desc = f"Moderate rise ({trend_pct:+.1f}%)"
            else:
                momentum = 5
                momentum_desc = f"Weak rise ({trend_pct:+.1f}%)"
        elif trend_direction == "falling":
            if trend_pct < -15:
                momentum = -15
                momentum_desc = f"⚠️ Strong fall ({trend_pct:.1f}%)"
            elif trend_pct < -5:
                momentum = -10
                momentum_desc = f"⚠️ Moderate fall ({trend_pct:.1f}%)"
            else:
                momentum = -5
                momentum_desc = f"⚠️ Weak fall ({trend_pct:.1f}%)"

        # Peak position bonus
        if vs_peak_pct is not None:
            if vs_peak_pct < -40 and (trend_direction != "falling" or trend_pct > -10):
                momentum += 10
                momentum_desc += f" + FAR below peak ({vs_peak_pct:.1f}%) = +10"
            elif vs_peak_pct < -25 and (trend_direction != "falling" or trend_pct > -10):
                momentum += 7
                momentum_desc += f" + Below peak ({vs_peak_pct:.1f}%) = +7"
            elif vs_peak_pct < -15 and trend_direction != "falling":
                momentum += 5
                momentum_desc += f" + Below peak ({vs_peak_pct:.1f}%) = +5"
            elif vs_peak_pct > -5 and trend_direction == "falling":
                momentum -= 5
                momentum_desc += " + At peak but falling = -5"

    momentum_color = "green" if momentum > 0 else "red" if momentum < 0 else "yellow"
    console.print(f"  5. Market Momentum: [{momentum_color}]{momentum:+}/15[/{momentum_color}]")
    console.print(f"     → {momentum_desc}")

    # Total
    total = points_efficiency + avg_efficiency + affordability + form + momentum
    total = max(total, 0)
    console.print(f"\n[bold]TOTAL VALUE SCORE: {total:.1f}/100[/bold]")

    # Explain ranking
    console.print("\n[bold]What this means:[/bold]")
    if total >= 80:
        console.print("  [green]⭐ EXCEPTIONAL - Top priority target[/green]")
    elif total >= 60:
        console.print("  [green]✓ GOOD - Strong opportunity[/green]")
    elif total >= 40:
        console.print("  [yellow]⚠️  DECENT - Consider if budget allows[/yellow]")
    elif total >= 20:
        console.print("  [yellow]⚠️  WEAK - Only if no better options[/yellow]")
    else:
        console.print("  [red]✗ POOR - Avoid[/red]")

    return total


def main():
    player_name = sys.argv[1] if len(sys.argv) > 1 else input("Enter player name to analyze: ")

    console.print("[dim]Loading config and connecting to KICKBASE...[/dim]")
    settings = load_config()
    api = KickbaseAPI(settings)
    api.login()

    leagues = api.get_leagues()
    league = leagues[0]

    console.print("[dim]Loading market data...[/dim]")
    market = api.get_market(league)
    kickbase_market = [p for p in market if p.is_kickbase_seller()]

    # Find player
    player = None
    for p in kickbase_market:
        full_name = f"{p.first_name} {p.last_name}"
        if player_name.lower() in full_name.lower():
            player = p
            break

    if not player:
        console.print(f"[red]Player '{player_name}' not found in market[/red]")
        console.print("\n[dim]Available players:[/dim]")
        for p in kickbase_market[:10]:
            console.print(f"  • {p.first_name} {p.last_name}")
        console.print("  ...")
        return

    # Get trend data
    console.print("[dim]Fetching trend data...[/dim]")
    trader = Trader(api, settings)
    trends = trader._fetch_player_trends([player], limit=1)
    trend = trends.get(player.id, {})

    # Explain score
    explain_value_score(player, trend)

    console.print(f"\n[bold cyan]{'=' * 60}[/bold cyan]\n")


if __name__ == "__main__":
    main()
