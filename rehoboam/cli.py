"""CLI interface for Rehoboam"""

import typer
from rich.console import Console
from rich.prompt import Confirm

from .api import KickbaseAPI
from .config import get_settings
from .trader import Trader

app = typer.Typer(
    name="rehoboam",
    help="KICKBASE Trading Bot - Automate your player trades",
    add_completion=False,
)
console = Console()


def get_api() -> KickbaseAPI:
    """Initialize and return the API client"""
    settings = get_settings()
    return KickbaseAPI(settings.kickbase_email, settings.kickbase_password)


@app.command()
def login():
    """Test KICKBASE login credentials"""
    console.print("[cyan]Testing KICKBASE login...[/cyan]")

    api = get_api()
    try:
        success = api.login()
        if success:
            console.print("[green]✓ Login successful![/green]")
            console.print(f"[green]  User: {api.user.name}[/green]")

            leagues = api.get_leagues()
            console.print(f"\n[cyan]Your leagues ({len(leagues)}):[/cyan]")
            for i, league in enumerate(leagues, 1):
                console.print(f"  {i}. {league.name}")
    except Exception as e:
        console.print(f"[red]✗ Login failed: {e}[/red]")
        raise typer.Exit(code=1) from e


@app.command()
def analyze(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
    show_all: bool = typer.Option(
        False, "--all", "-a", help="Show all players, not just opportunities"
    ),
):
    """Analyze the market for trading opportunities"""

    settings = get_settings()
    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if not leagues:
        console.print("[red]No leagues found[/red]")
        raise typer.Exit(code=1)

    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"\n[bold]Analyzing league: {league.name}[/bold]\n")

    trader = Trader(api, settings)

    # Analyze market
    market_analyses = trader.analyze_market(league)

    if show_all:
        trader.display_analysis(market_analyses, title="All Market Players")
    else:
        opportunities = trader.analyzer.find_best_opportunities(market_analyses, top_n=20)
        if opportunities:
            trader.display_analysis(opportunities, title="Top 20 Trading Opportunities")
        else:
            console.print("[yellow]No trading opportunities found[/yellow]")

    # Analyze your team
    console.print("\n")
    team_analyses = trader.analyze_team(league)

    # Always show full squad overview (like we do for market)
    trader.display_sell_analysis(team_analyses, title="📊 Your Squad Analysis", league=league)

    # Show recommendation breakdown
    sell_count = sum(1 for a in team_analyses if a.recommendation == "SELL")
    hold_count = sum(1 for a in team_analyses if a.recommendation == "HOLD")

    console.print(f"\n📋 Recommendations: {sell_count} SELL, {hold_count} HOLD")

    if sell_count > 0:
        console.print(f"[red]⚠️  Found {sell_count} player(s) you should consider selling![/red]")
    else:
        console.print("[green]✓ No urgent sell recommendations - all players worth holding[/green]")

    # Find profit trading opportunities (buy low, sell high)
    console.print("\n")
    try:
        # Get current budget for display
        team_info = trader.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)

        profit_opps = trader.find_profit_opportunities(league)
        trader.display_profit_opportunities(profit_opps, current_budget=current_budget)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not analyze profit opportunities: {e}[/yellow]")

    # Find N-for-M trade opportunities (improve lineup)
    console.print("\n")
    try:
        trades = trader.find_trade_opportunities(league)
        trader.display_trade_recommendations(trades)
    except Exception as e:
        console.print(
            f"[yellow]Warning: Could not analyze lineup trade opportunities: {e}[/yellow]"
        )

    # Debug info for first-time users
    if show_all or sell_count == 0:
        # Show reasons for HOLD recommendations
        hold_reasons = {}
        for a in team_analyses:
            if a.recommendation == "HOLD":
                reason_key = a.reason.split(" | ")[0]  # Get first part of reason
                hold_reasons[reason_key] = hold_reasons.get(reason_key, 0) + 1

        if hold_reasons:
            console.print("\n[dim]Why players are on HOLD:[/dim]")
            for reason, count in hold_reasons.items():
                console.print(f"[dim]  • {count}x: {reason}[/dim]")


@app.command()
def trade(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
    max_trades: int = typer.Option(5, "--max", "-m", help="Maximum number of trades to execute"),
    live: bool = typer.Option(False, "--live", help="Execute real trades (overrides DRY_RUN)"),
):
    """Start automated trading"""

    settings = get_settings()

    # Override dry_run if --live flag is used
    if live:
        if not Confirm.ask(
            "[yellow]⚠️  You are about to execute REAL trades. Are you sure?[/yellow]"
        ):
            console.print("[red]Trading cancelled[/red]")
            raise typer.Exit(code=0)
        settings.dry_run = False

    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if not leagues:
        console.print("[red]No leagues found[/red]")
        raise typer.Exit(code=1)

    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"\n[bold]Trading in league: {league.name}[/bold]")

    trader = Trader(api, settings)
    trader.auto_trade(league, max_trades=max_trades)


@app.command()
def monitor(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Continuously monitor bids until resolved"
    ),
    live: bool = typer.Option(
        False, "--live", help="Execute real sales when bids win (overrides DRY_RUN)"
    ),
):
    """Monitor pending bids and execute safe replacements"""

    settings = get_settings()

    # Override dry_run if --live flag is used
    if live:
        if not Confirm.ask(
            "[yellow]⚠️  You are about to execute REAL sales when bids win. Are you sure?[/yellow]"
        ):
            console.print("[red]Monitoring cancelled[/red]")
            raise typer.Exit(code=0)
        settings.dry_run = False

    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if not leagues:
        console.print("[red]No leagues found[/red]")
        raise typer.Exit(code=1)

    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"\n[bold]Monitoring bids in league: {league.name}[/bold]\n")

    trader = Trader(api, settings)

    # Get pending bids summary
    summary = trader.bid_monitor.get_pending_summary()
    console.print(f"[cyan]{summary}[/cyan]\n")

    if watch:
        # Continuously monitor until all resolved
        trader.bid_monitor.monitor_all_bids(league, dry_run=settings.dry_run)
    else:
        # Single check
        pending_bids = [
            pid
            for pid, status in trader.bid_monitor.pending_bids.items()
            if status.status == "pending"
        ]

        if not pending_bids:
            console.print("[yellow]No pending bids to check[/yellow]")
            return

        for player_id in pending_bids:
            trader.bid_monitor.execute_replacement_if_won(
                league=league, player_id=player_id, dry_run=settings.dry_run
            )


@app.command()
def register_bid(
    player_name: str = typer.Argument(..., help="Player name to search for"),
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
):
    """Manually register an existing bid for monitoring"""

    settings = get_settings()
    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if not leagues:
        console.print("[red]No leagues found[/red]")
        raise typer.Exit(code=1)

    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"\n[bold]Searching for bids in league: {league.name}[/bold]\n")

    trader = Trader(api, settings)

    # Search market for player with your bid
    market_players = api.get_market(league)
    user_id = api.user.id

    matching_players = []
    for player in market_players:
        # Check if name matches
        full_name = f"{player.first_name} {player.last_name}"
        if player_name.lower() in full_name.lower():
            # Check if we have a bid on this player
            if player.has_user_offer(user_id):
                matching_players.append(player)

    if not matching_players:
        console.print(f"[yellow]No active bids found for '{player_name}'[/yellow]")
        console.print("[dim]Make sure you have an active bid on this player[/dim]")
        raise typer.Exit(code=0)

    if len(matching_players) > 1:
        console.print(f"[yellow]Multiple players found matching '{player_name}':[/yellow]")
        for i, player in enumerate(matching_players, 1):
            console.print(
                f"  {i}. {player.first_name} {player.last_name} - €{player.user_offer_price:,}"
            )
        console.print("[dim]Please be more specific with the player name[/dim]")
        raise typer.Exit(code=0)

    player = matching_players[0]
    console.print(f"[green]Found bid on {player.first_name} {player.last_name}[/green]")
    console.print(f"  Your bid: €{player.user_offer_price:,}")
    console.print(f"  Asking price: €{player.price:,}")
    console.print(f"  Listed at: {player.listed_at}")

    # Check if already registered
    if player.id in trader.bid_monitor.pending_bids:
        existing = trader.bid_monitor.pending_bids[player.id]
        console.print(
            f"\n[yellow]This bid is already registered (status: {existing.status})[/yellow]"
        )
        raise typer.Exit(code=0)

    # Ask if they want to register
    if not Confirm.ask("\n[cyan]Register this bid for monitoring?[/cyan]"):
        console.print("[yellow]Cancelled[/yellow]")
        raise typer.Exit(code=0)

    # Register the bid
    trader.bid_monitor.register_bid(
        player_id=player.id,
        player_name=f"{player.first_name} {player.last_name}",
        bid_amount=player.user_offer_price,
    )

    console.print("\n[green]✓ Bid registered successfully![/green]")
    console.print("\n[cyan]To monitor this bid, run:[/cyan]")
    console.print("  rehoboam monitor --watch")
    console.print("\n[cyan]To monitor and auto-execute replacements (when implemented):[/cyan]")
    console.print("  rehoboam monitor --watch --live")


@app.command()
def record_purchase(
    player_name: str = typer.Argument(..., help="Player name (e.g., 'Danel Sinani')"),
    purchase_price: int = typer.Argument(
        ..., help="What you paid in euros (e.g., 6000000 for €6M)"
    ),
    league_index: int = typer.Option(0, "--league", "-l", help="League index"),
):
    """Record purchase price for an existing player in your squad"""

    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]

    # Get squad
    console.print(f"[cyan]Fetching your squad from {league.name}...[/cyan]")
    players = api.get_squad(league)

    # Find player by name
    player_name_lower = player_name.lower()
    matching_players = [
        p for p in players if player_name_lower in f"{p.first_name} {p.last_name}".lower()
    ]

    if not matching_players:
        console.print(f"[red]Could not find '{player_name}' in your squad[/red]")
        console.print("\n[yellow]Your squad:[/yellow]")
        for p in players:
            console.print(f"  • {p.first_name} {p.last_name} ({p.position})")
        raise typer.Exit(code=1)

    if len(matching_players) > 1:
        console.print("[yellow]Multiple matches found:[/yellow]")
        for p in matching_players:
            console.print(f"  • {p.first_name} {p.last_name} ({p.position})")
        console.print("[yellow]Please be more specific[/yellow]")
        raise typer.Exit(code=1)

    player = matching_players[0]
    full_name = f"{player.first_name} {player.last_name}"

    # Record purchase
    from .value_tracker import ValueTracker

    tracker = ValueTracker()
    tracker.record_purchase(
        player_id=player.id,
        player_name=full_name,
        league_id=league.id,
        purchase_price=purchase_price,
    )

    # Calculate profit
    current_value = player.market_value
    profit = current_value - purchase_price
    profit_pct = (profit / purchase_price) * 100 if purchase_price > 0 else 0

    console.print(f"\n[green]✓ Recorded purchase for {full_name}[/green]")
    console.print(f"  Purchase price: €{purchase_price:,}")
    console.print(f"  Current value: €{current_value:,}")
    console.print(f"  Profit/Loss: €{profit:,} ({profit_pct:+.1f}%)")

    if profit_pct > 50:
        console.print("\n[bright_green]💰 Excellent profit! Consider selling soon.[/bright_green]")
    elif profit_pct < -10:
        console.print("\n[red]📉 Losing value. Monitor closely.[/red]")

    console.print("\n[dim]Run 'rehoboam analyze' to see updated squad analysis[/dim]")


@app.command()
def config():
    """Show current configuration"""
    try:
        settings = get_settings()

        console.print("\n[bold cyan]Current Configuration:[/bold cyan]\n")
        console.print(f"  Email: {settings.kickbase_email}")
        console.print(f"  Min Sell Profit: {settings.min_sell_profit_pct}%")
        console.print(f"  Max Loss: {settings.max_loss_pct}%")
        console.print(f"  Min Buy Value Increase: {settings.min_buy_value_increase_pct}%")
        console.print(f"  Max Player Cost: €{settings.max_player_cost:,}")
        console.print(f"  Reserve Budget: €{settings.reserve_budget:,}")
        console.print(f"  Dry Run: {settings.dry_run}")
        console.print("\n[dim]Edit .env file to change configuration[/dim]\n")

    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        console.print(
            "[yellow]Make sure you have created a .env file (copy from .env.example)[/yellow]"
        )
        raise typer.Exit(code=1) from e


@app.command()
def stats(
    show_patterns: bool = typer.Option(
        False, "--patterns", "-p", help="Show detailed pattern analysis"
    ),
):
    """View bot learning statistics and recommendations"""
    from .bid_learner import BidLearner

    learner = BidLearner()

    console.print("\n[bold cyan]📊 Bot Learning Statistics[/bold cyan]\n")

    # Auction outcomes
    auction_stats = learner.get_statistics()

    console.print("[bold]Auction Performance:[/bold]")
    console.print(f"  Total auctions: {auction_stats['total_auctions']}")
    console.print(
        f"  Wins: [green]{auction_stats['wins']}[/green] | Losses: [red]{auction_stats['losses']}[/red]"
    )
    console.print(f"  Win rate: [cyan]{auction_stats['win_rate']}%[/cyan]")

    if auction_stats["total_auctions"] > 0:
        console.print(
            f"  Avg winning overbid: [green]{auction_stats['avg_winning_overbid']}%[/green]"
        )
        console.print(f"  Avg losing overbid: [red]{auction_stats['avg_losing_overbid']}%[/red]")
        console.print(
            f"  Avg value score (wins): {auction_stats.get('avg_value_score_wins', 0):.1f}"
        )
        console.print(
            f"  Avg value score (losses): {auction_stats.get('avg_value_score_losses', 0):.1f}"
        )

    # Flip outcomes
    console.print("\n[bold]Flip Performance:[/bold]")
    flip_stats = learner.get_flip_statistics()

    console.print(f"  Total flips: {flip_stats['total_flips']}")

    if flip_stats["total_flips"] > 0:
        console.print(
            f"  Profitable: [green]{flip_stats['profitable_flips']}[/green] | Unprofitable: [red]{flip_stats['unprofitable_flips']}[/red]"
        )
        console.print(f"  Success rate: [cyan]{flip_stats['success_rate']}%[/cyan]")
        console.print(f"  Avg profit: [green]+{flip_stats['avg_profit_pct']}%[/green]")
        console.print(f"  Avg loss: [red]{flip_stats['avg_loss_pct']}%[/red]")
        console.print(f"  Avg hold time (profit): {flip_stats['avg_hold_days_profit']:.0f} days")
        console.print(f"  Avg hold time (loss): {flip_stats['avg_hold_days_loss']:.0f} days")
        console.print(f"  Total profit: [cyan]€{flip_stats['total_profit']:,}[/cyan]")

        if flip_stats["best_flip"]:
            best = flip_stats["best_flip"]
            console.print(
                f"\n  🏆 Best flip: {best['player']} - €{best['profit']:,} ({best['profit_pct']:+.1f}%) in {best['hold_days']} days"
            )

        if flip_stats["worst_flip"]:
            worst = flip_stats["worst_flip"]
            console.print(
                f"  📉 Worst flip: {worst['player']} - €{worst['profit']:,} ({worst['profit_pct']:+.1f}%) in {worst['hold_days']} days"
            )

    # Recommendations
    console.print("\n[bold]💡 Learning Recommendations:[/bold]")
    recommendations = learner.get_learning_recommendations()

    for rec in recommendations:
        console.print(f"  • {rec}")

    # Detailed patterns
    if show_patterns and flip_stats["total_flips"] > 0:
        console.print("\n[bold]📈 Detailed Pattern Analysis:[/bold]")
        patterns = learner.analyze_flip_patterns()

        if patterns.get("by_trend"):
            console.print("\n[cyan]By Trend:[/cyan]")
            for trend, stats in patterns["by_trend"].items():
                console.print(
                    f"  {trend}: {stats['count']} flips | {stats['avg_profit_pct']:+.1f}% avg | {stats['success_rate']}% success"
                )

        if patterns.get("by_position"):
            console.print("\n[cyan]By Position:[/cyan]")
            for position, stats in sorted(
                patterns["by_position"].items(), key=lambda x: x[1]["avg_profit_pct"], reverse=True
            ):
                console.print(
                    f"  {position}: {stats['count']} flips | {stats['avg_profit_pct']:+.1f}% avg | {stats['success_rate']}% success"
                )

        if patterns.get("by_hold_time"):
            console.print("\n[cyan]By Hold Time:[/cyan]")
            for period, stats in patterns["by_hold_time"].items():
                console.print(
                    f"  {period}: {stats['count']} flips | {stats['avg_profit_pct']:+.1f}% avg | {stats['success_rate']}% success"
                )

        if patterns.get("by_injury_status"):
            console.print("\n[cyan]By Injury Status:[/cyan]")
            for status, stats in patterns["by_injury_status"].items():
                console.print(
                    f"  {status}: {stats['count']} flips | {stats['avg_profit_pct']:+.1f}% avg | {stats['success_rate']}% success"
                )

    if auction_stats["total_auctions"] == 0 and flip_stats["total_flips"] == 0:
        console.print(
            "\n[yellow]No data collected yet. Start trading to build learning database![/yellow]"
        )
        console.print("[dim]Run 'rehoboam auto' to start automated trading[/dim]")

    console.print()


@app.command()
def auto(
    league_index: int = typer.Option(0, help="League index (0 for first league)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate trades without executing"),
    max_trades: int = typer.Option(3, "--max-trades", help="Max trades per session"),
    max_spend: int = typer.Option(50_000_000, "--max-spend", help="Max daily spend"),
):
    """Run a single automated trading session (profit + lineup)"""
    from .auto_trader import AutoTrader

    console.print("[bold cyan]🤖 Automated Trading Session[/bold cyan]")
    if dry_run:
        console.print("[yellow]DRY RUN MODE - No trades will be executed[/yellow]")

    # Initialize
    api = get_api()
    settings = get_settings()

    console.print("[cyan]Logging in...[/cyan]")
    api.login()
    console.print(f"[green]✓ Logged in as {api.user.name}[/green]")

    # Get league
    leagues = api.get_leagues()
    if league_index >= len(leagues):
        console.print(f"[red]League index {league_index} not found[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"[cyan]Trading in league: {league.name}[/cyan]\n")

    # Run auto trader
    auto_trader = AutoTrader(
        api=api,
        settings=settings,
        max_trades_per_session=max_trades,
        max_daily_spend=max_spend,
        dry_run=dry_run,
    )

    session = auto_trader.run_full_session(league)

    # Show summary
    console.print("\n[bold]Session Complete![/bold]")
    console.print(f"Duration: {session.end_time - session.start_time:.1f}s")
    console.print(f"Profit trades executed: {len([r for r in session.profit_trades if r.success])}")
    console.print(f"Lineup trades executed: {len([r for r in session.lineup_trades if r.success])}")

    if session.net_change != 0:
        color = "green" if session.net_change > 0 else "red"
        console.print(f"Net budget change: [{color}]€{session.net_change:,}[/{color}]")


@app.command()
def daemon(
    interval: int = typer.Option(120, "--interval", help="Minutes between sessions"),
    start_hour: int = typer.Option(8, "--start-hour", help="Trading start hour (0-23)"),
    end_hour: int = typer.Option(22, "--end-hour", help="Trading end hour (0-23)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate trades without executing"),
    max_trades: int = typer.Option(3, "--max-trades", help="Max trades per session"),
    max_spend: int = typer.Option(50_000_000, "--max-spend", help="Max daily spend"),
):
    """Run automated trading scheduler in background"""
    from .scheduler import TradingScheduler

    console.print("[bold cyan]🤖 Starting Automated Trading Daemon[/bold cyan]")
    console.print(f"Interval: Every {interval} minutes")
    console.print(f"Trading hours: {start_hour}:00 - {end_hour}:00")
    console.print(f"Max trades/session: {max_trades}")
    console.print(f"Max daily spend: €{max_spend:,}")
    if dry_run:
        console.print("[yellow]DRY RUN MODE - No trades will be executed[/yellow]")
    console.print("\nPress Ctrl+C to stop\n")

    scheduler = TradingScheduler(
        interval_minutes=interval,
        trading_hours_start=start_hour,
        trading_hours_end=end_hour,
        dry_run=dry_run,
        max_trades_per_session=max_trades,
        max_daily_spend=max_spend,
    )

    try:
        scheduler.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped by user[/yellow]")


@app.callback()
def callback():
    """
    Rehoboam - KICKBASE Trading Bot

    Automate your KICKBASE trading strategy with market value tracking.
    """
    pass


if __name__ == "__main__":
    app()
