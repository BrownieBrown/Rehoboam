"""CLI interface for Rehoboam"""

import typer
from rich.console import Console
from rich.prompt import Confirm

from .api import KickbaseAPI
from .bid_learner import BidLearner
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


def get_learner() -> BidLearner:
    """Initialize and return the bid learner for adaptive bidding"""
    return BidLearner()


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
    detailed: bool = typer.Option(
        False, "--detailed", "-d", help="Show detailed analysis (default is compact action plan)"
    ),
    show_all: bool = typer.Option(
        False, "--all", "-a", help="Show all players, not just opportunities (detailed mode only)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed debug information"),
    simple: bool = typer.Option(
        False, "--simple", "-s", help="Simple mode - skip predictions and advanced analysis"
    ),
    show_risk: bool = typer.Option(
        False,
        "--risk",
        "-r",
        help="Show risk metrics (volatility, VaR, Sharpe ratio) (detailed mode)",
    ),
    show_opportunity_cost: bool = typer.Option(
        False,
        "--opportunity-cost",
        "-oc",
        help="Show trade-off analysis for purchases (detailed mode)",
    ),
    show_portfolio: bool = typer.Option(
        False,
        "--portfolio",
        "-p",
        help="Show portfolio-level metrics (diversification, risk, projections) (detailed mode)",
    ),
):
    """Analyze the market for trading opportunities (compact action plan by default)"""

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

    # Print clean header
    console.print("\n" + "═" * 60)
    console.print(f"[bold cyan]🏆  {league.name}[/bold cyan]")
    console.print("═" * 60 + "\n")

    # Auto-sync activity feed for learning (silent background operation)
    try:
        from .activity_feed_learner import ActivityFeedLearner

        feed_learner = ActivityFeedLearner()
        activities = api.client.get_activities_feed(league.id, start=0)
        stats = feed_learner.process_activity_feed(activities, api_client=api.client)

        if verbose and (stats["transfers_new"] > 0 or stats["market_values_new"] > 0):
            console.print(
                f"[dim]✓ Synced activity feed: {stats['transfers_new']} new transfers, {stats['market_values_new']} new market values[/dim]"
            )
    except Exception as e:
        if verbose:
            console.print(f"[dim]Warning: Could not sync activity feed: {e}[/dim]")

    # Connect learners for adaptive bidding + competitive intelligence
    learner = get_learner()
    trader = Trader(
        api, settings, verbose=verbose, bid_learner=learner, activity_feed_learner=feed_learner
    )

    # Analyze market (suppress verbose output unless requested)
    if not verbose:
        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = StringIO()

    market_analyses = trader.analyze_market(league, calculate_risk=show_risk)

    if not verbose:
        sys.stdout = old_stdout

    # Get team info early for budget display
    team_info = trader.api.get_team_info(league)
    current_budget = team_info.get("budget", 0)

    # Use compact display by default, detailed only if requested
    if not detailed:
        # COMPACT MODE - Action plan focused display
        trader.display_compact_action_plan(league, market_analyses, current_budget)
        return

    # DETAILED MODE - Full analysis (original behavior)
    # SECTION 1: Market Opportunities
    console.print("[bold cyan]📈 MARKET OPPORTUNITIES[/bold cyan]")
    console.print("─" * 60 + "\n")

    if show_all:
        trader.display_analysis(market_analyses, title="All Market Players", show_risk=show_risk)
    else:
        opportunities = trader.analyzer.find_best_opportunities(market_analyses, top_n=8)
        if opportunities:
            trader.display_analysis(
                opportunities,
                title="Top Buy Recommendations",
                show_risk=show_risk,
                show_bids=True,
            )

            # Add helpful context about bidding and market timing
            console.print("\n[dim]💡 Tips:[/dim]")
            console.print(
                "[dim]  • Smart Bid shows recommended bid amount (+% over asking price)[/dim]"
            )
            console.print(
                "[dim]  • Days Listed shows time on market with predicted price change[/dim]"
            )
            console.print(
                "[dim]  • Value Score combines performance, trends, and matchups (40+ = buy threshold)[/dim]"
            )
            console.print(
                "[dim]  • Run 'rehoboam market-intel' to see learned price adjustment patterns[/dim]"
            )

            # Show opportunity cost analysis if enabled
            if show_opportunity_cost:
                console.print("\n" + "─" * 60)
                console.print("[bold cyan]💡 OPPORTUNITY COST ANALYSIS[/bold cyan]")
                console.print("─" * 60 + "\n")
                trader.display_opportunity_costs(opportunities, league, max_opportunities=3)
        else:
            console.print("[yellow]No trading opportunities found[/yellow]")

    # SECTION 2: Your Squad
    console.print("\n" + "═" * 60)
    console.print("[bold cyan]📊 YOUR SQUAD ANALYSIS[/bold cyan]")
    console.print("═" * 60 + "\n")

    team_analyses = trader.analyze_team(league)

    # Squad Optimization - Get recommendations FIRST to coordinate with individual analysis
    squad_optimization = None
    try:
        squad_optimization = trader.optimize_squad_for_gameday(league)
        if squad_optimization and squad_optimization.players_to_sell:
            # Update recommendations to match squad optimizer
            players_to_sell_ids = {p.id for p in squad_optimization.players_to_sell}
            for analysis in team_analyses:
                if analysis.player.id in players_to_sell_ids:
                    # Override recommendation to SELL for budget management
                    old_rec = analysis.recommendation
                    analysis.recommendation = "SELL"
                    if old_rec == "HOLD":
                        # Update reason to explain why
                        analysis.reason = f"Bench player - sell to clear debt | {analysis.reason}"
                        analysis.confidence = 0.8
    except Exception:
        pass  # Silent failure for squad optimization

    # Show squad analysis
    trader.display_sell_analysis(team_analyses, title="Your Squad", league=league)

    # Show recommendation breakdown
    sell_count = sum(1 for a in team_analyses if a.recommendation == "SELL")
    hold_count = sum(1 for a in team_analyses if a.recommendation == "HOLD")

    console.print(f"\n[bold]Summary:[/bold] {sell_count} to sell, {hold_count} to hold")

    if sell_count > 0:
        console.print(f"[red]⚠️  {sell_count} player(s) recommended for sale[/red]")
    else:
        console.print("[green]✓ No urgent sell recommendations[/green]")

    # Squad Balance & Composition (enhanced feature - always show unless simple mode)
    if not simple:
        try:
            from .enhanced_analyzer import EnhancedAnalyzer

            enhanced_analyzer = EnhancedAnalyzer()

            console.print("\n" + "─" * 60)
            squad_balance = enhanced_analyzer.analyze_squad_balance(team_analyses)
            enhanced_analyzer.display_squad_balance(squad_balance, current_budget=current_budget)
        except Exception as e:
            if verbose:
                console.print(f"[red]Squad balance error: {e}[/red]")

    # Show Squad Optimization details
    if squad_optimization:
        try:
            from .squad_optimizer import SquadOptimizer

            optimizer = SquadOptimizer()
            console.print("\n" + "─" * 60)
            optimizer.display_optimization(
                squad_optimization,
                player_values=trader.get_player_values_from_analyses(team_analyses),
            )
        except Exception:
            pass

    # Portfolio Analysis
    if show_portfolio:
        try:
            from .portfolio_analyzer import PortfolioAnalyzer

            portfolio_analyzer = PortfolioAnalyzer()
            console.print("\n" + "─" * 60)

            # Analyze portfolio
            portfolio_metrics = portfolio_analyzer.analyze_portfolio(
                squad_analyses=team_analyses,
                market_analyses=market_analyses,
                current_budget=current_budget,
            )

            # Display metrics
            portfolio_analyzer.display_portfolio_metrics(portfolio_metrics)
        except Exception as e:
            if verbose:
                console.print(f"[red]Portfolio analysis error: {e}[/red]")

    # SECTION 3: Trading Strategies
    console.print("\n" + "═" * 60)
    console.print("[bold cyan]💡 TRADING STRATEGIES[/bold cyan]")
    console.print("═" * 60 + "\n")

    # Profit trading opportunities (limit to top 5 for cleaner output)
    try:
        profit_opps = trader.find_profit_opportunities(league)
        if profit_opps:
            # Limit to top 5 opportunities for cleaner output
            profit_opps = profit_opps[:5]
            console.print("[bold]Strategy 1: Quick Profit Flips[/bold]")
            console.print("Buy undervalued, hold 3-7 days, sell for profit\n")
            trader.display_profit_opportunities(profit_opps, current_budget=current_budget)
    except Exception as e:
        if verbose:
            console.print(f"[red]Profit opportunities error: {e}[/red]")

    # N-for-M trade opportunities (limit to top 3 for cleaner output)
    try:
        trades = trader.find_trade_opportunities(league)
        if trades:
            # Limit to top 3 trades for cleaner output
            trades = trades[:3]
            console.print("\n[bold]Strategy 2: Lineup Upgrades[/bold]")
            console.print("Trade combinations to improve your best 11\n")
            trader.display_trade_recommendations(trades)
    except Exception as e:
        if verbose:
            console.print(f"[red]Trade opportunities error: {e}[/red]")

    # SECTION 4: Predictions & Insights (enhanced - skip in simple mode)
    if not simple:
        console.print("\n" + "═" * 60)
        console.print("[bold cyan]🔮 VALUE PREDICTIONS & INSIGHTS[/bold cyan]")
        console.print("═" * 60 + "\n")

        try:
            from .enhanced_analyzer import EnhancedAnalyzer

            enhanced_analyzer = EnhancedAnalyzer()

            # Position Landscape Analysis
            console.print("[bold]Market Analysis by Position[/bold]")
            console.print("Compare opportunities across different positions\n")
            position_comparisons = enhanced_analyzer.analyze_position_landscape(market_analyses)
            enhanced_analyzer.display_position_comparison(position_comparisons)

            # Value Predictions for your squad
            console.print("\n" + "─" * 60)
            console.print("[bold]Your Squad - Value Predictions[/bold]")
            console.print("Predicted market value changes for your players\n")

            squad_predictions = []
            for analysis in team_analyses[:8]:  # Top 8 players
                try:
                    # Get trend data using v2 endpoint (has actual historical data)
                    history_data = trader.api.client.get_player_market_value_history_v2(
                        player_id=analysis.player.id, timeframe=92  # 3 months
                    )

                    # Extract trend from historical data
                    it_array = history_data.get("it", [])
                    trend_analysis = {"has_data": False}

                    if it_array and len(it_array) >= 14:
                        recent = it_array[-14:]
                        first_value = recent[0].get("mv", 0)
                        last_value = recent[-1].get("mv", 0)

                        if first_value > 0:
                            trend_pct = ((last_value - first_value) / first_value) * 100

                            # Long-term trend
                            long_term_pct = 0
                            if len(it_array) >= 30:
                                month_ago_value = it_array[-30].get("mv", 0)
                                if month_ago_value > 0:
                                    long_term_pct = (
                                        (last_value - month_ago_value) / month_ago_value
                                    ) * 100

                            trend_analysis = {
                                "has_data": True,
                                "trend_pct": trend_pct,
                                "long_term_pct": long_term_pct,
                                "peak_value": history_data.get("hmv", 0),
                                "current_value": last_value,
                            }

                    # Get performance data
                    perf_data = trader.history_cache.get_cached_performance(
                        player_id=analysis.player.id, league_id=league.id, max_age_hours=24
                    )
                    if not perf_data:
                        perf_data = trader.api.client.get_player_performance(
                            league.id, analysis.player.id
                        )

                    prediction = enhanced_analyzer.predict_player_value(
                        analysis.player, trend_analysis, perf_data
                    )
                    squad_predictions.append(prediction)
                except Exception as e:
                    if verbose:
                        console.print(
                            f"[dim]Prediction error for {analysis.player.first_name}: {e}[/dim]"
                        )
                    pass

            if squad_predictions:
                enhanced_analyzer.display_predictions(squad_predictions, title="")
            else:
                console.print("[dim]No prediction data available for your squad[/dim]")

            # Market Predictions for top opportunities
            console.print("\n" + "─" * 60)
            console.print("[bold]Market Opportunities - Value Predictions[/bold]")
            console.print("Players expected to gain value - buy before they rise!\n")

            market_predictions = []
            top_market_players = trader.analyzer.find_best_opportunities(market_analyses, top_n=8)

            for analysis in top_market_players:
                try:
                    # Get trend data using v2 endpoint (has actual historical data)
                    history_data = trader.api.client.get_player_market_value_history_v2(
                        player_id=analysis.player.id, timeframe=92  # 3 months
                    )

                    # Extract trend from historical data
                    it_array = history_data.get("it", [])
                    trend_analysis = {"has_data": False}

                    if it_array and len(it_array) >= 14:
                        recent = it_array[-14:]
                        first_value = recent[0].get("mv", 0)
                        last_value = recent[-1].get("mv", 0)

                        if first_value > 0:
                            trend_pct = ((last_value - first_value) / first_value) * 100

                            # Long-term trend
                            long_term_pct = 0
                            if len(it_array) >= 30:
                                month_ago_value = it_array[-30].get("mv", 0)
                                if month_ago_value > 0:
                                    long_term_pct = (
                                        (last_value - month_ago_value) / month_ago_value
                                    ) * 100

                            trend_analysis = {
                                "has_data": True,
                                "trend_pct": trend_pct,
                                "long_term_pct": long_term_pct,
                                "peak_value": history_data.get("hmv", 0),
                                "current_value": last_value,
                            }

                    # Get performance data
                    perf_data = trader.history_cache.get_cached_performance(
                        player_id=analysis.player.id, league_id=league.id, max_age_hours=24
                    )
                    if not perf_data:
                        perf_data = trader.api.client.get_player_performance(
                            league.id, analysis.player.id
                        )

                    prediction = enhanced_analyzer.predict_player_value(
                        analysis.player, trend_analysis, perf_data
                    )
                    market_predictions.append(prediction)
                except Exception as e:
                    if verbose:
                        console.print(
                            f"[dim]Prediction error for {analysis.player.first_name}: {e}[/dim]"
                        )
                    pass

            if market_predictions:
                enhanced_analyzer.display_predictions(market_predictions, title="")
            else:
                console.print("[dim]No prediction data available for market opportunities[/dim]")

            # Tips and recommendations
            console.print("\n" + "─" * 60)
            console.print("[bold]💡 Quick Tips[/bold]\n")
            console.print("📈 [green]Improving[/green] - Strong upward momentum, buy now")
            console.print("📉 [red]Declining[/red] - Losing value, sell or avoid")
            console.print("➡️  [yellow]Stable[/yellow] - Predictable, safe hold")
            console.print("🌊 [magenta]Volatile[/magenta] - Unpredictable, risky\n")

        except Exception as e:
            if verbose:
                console.print(f"[red]Predictions error: {e}[/red]")
            pass  # Silent failure for predictions

    # Final summary
    console.print("\n" + "═" * 60)
    console.print("[bold]Analysis complete![/bold]")
    if simple:
        console.print("[dim]Run without --simple for predictions and insights[/dim]")
    console.print("═" * 60 + "\n")


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

    # Connect learners for adaptive bidding + competitive intelligence
    from .activity_feed_learner import ActivityFeedLearner

    learner = get_learner()
    feed_learner = ActivityFeedLearner()
    trader = Trader(api, settings, bid_learner=learner, activity_feed_learner=feed_learner)
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

    # Connect learners for adaptive bidding + competitive intelligence
    from .activity_feed_learner import ActivityFeedLearner

    learner = get_learner()
    feed_learner = ActivityFeedLearner()
    trader = Trader(api, settings, bid_learner=learner, activity_feed_learner=feed_learner)

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

    # Connect learners for adaptive bidding + competitive intelligence
    from .activity_feed_learner import ActivityFeedLearner

    learner = get_learner()
    feed_learner = ActivityFeedLearner()
    trader = Trader(api, settings, bid_learner=learner, activity_feed_learner=feed_learner)

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
        asking_price=player.price,
        market_value=player.market_value,
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
def market_intel(
    show_recent: bool = typer.Option(False, "--recent", "-r", help="Show recent price adjustments"),
    hours: int = typer.Option(24, "--hours", "-h", help="Hours of recent history to show"),
):
    """View market price adjustment patterns and intelligence"""
    from .market_price_tracker import MarketPriceTracker

    tracker = MarketPriceTracker()

    # Show learned patterns
    tracker.display_adjustment_patterns()

    # Show recent adjustments if requested
    if show_recent:
        tracker.display_recent_adjustments(hours=hours)


@app.command()
def sync_activity_feed(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
):
    """Sync activity feed to learn from all league transfers"""
    from .activity_feed_learner import ActivityFeedLearner

    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"\n[bold cyan]Syncing activity feed for: {league.name}[/bold cyan]\n")

    learner = ActivityFeedLearner()

    # Fetch and process activity feed
    console.print("[dim]Fetching activity feed...[/dim]")
    activities = api.client.get_activities_feed(league.id, start=0)

    console.print("[dim]Processing activities...[/dim]")
    stats = learner.process_activity_feed(activities, api_client=api.client)

    console.print("\n[green]✓ Sync complete![/green]")
    console.print(f"  New transfers: {stats['transfers_new']}")
    console.print(f"  New market values: {stats['market_values_new']}")
    console.print(
        f"  Duplicates skipped: {stats['transfers_duplicate'] + stats['market_values_duplicate']}"
    )

    # Display league stats
    learner.display_league_stats()

    console.print("[dim]Run 'rehoboam stats' to see overall learning statistics[/dim]\n")


@app.command()
def competitors(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
    competitor_name: str = typer.Option(None, "--name", "-n", help="Analyze specific competitor"),
):
    """Analyze competitor bidding patterns and threats"""
    from .activity_feed_learner import ActivityFeedLearner

    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"\n[bold cyan]Analyzing competitors in: {league.name}[/bold cyan]")

    learner = ActivityFeedLearner()

    if competitor_name:
        # Analyze specific competitor
        analysis = learner.get_competitor_analysis(competitor_name)

        if analysis["purchases"] == 0:
            console.print(f"\n[yellow]{analysis['message']}[/yellow]\n")
            return

        console.print(f"\n[bold red]Competitor Profile: {competitor_name}[/bold red]\n")
        console.print(f"Total purchases: {analysis['purchases']}")
        console.print(f"Average price: €{analysis['avg_price']:,}")
        console.print(f"Price range: €{analysis['min_price']:,} - €{analysis['max_price']:,}")
        console.print(f"Recent activity: {analysis['recent_purchases']} purchases in last 7 days")
        console.print(f"Aggression level: {analysis['aggression_level']}")

        if analysis.get("expensive_buys"):
            console.print("\n[bold]Most Expensive Purchases:[/bold]")
            for buy in analysis["expensive_buys"]:
                console.print(f"  • {buy['player']}: €{buy['price']:,}")

        console.print()
    else:
        # Display full competitor threat analysis
        learner.display_competitor_analysis()


@app.command()
def stats(
    show_patterns: bool = typer.Option(
        False, "--patterns", "-p", help="Show detailed pattern analysis"
    ),
    show_learning: bool = typer.Option(
        False, "--learning", "-l", help="Show factor performance and learning insights"
    ),
    show_league: bool = typer.Option(
        False, "--league", help="Show league transfer statistics from activity feed"
    ),
):
    """View bot learning statistics and recommendations"""
    from .bid_learner import BidLearner
    from .historical_tracker import HistoricalTracker

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

    # Learning insights (factor performance)
    if show_learning:
        console.print("\n" + "═" * 60)
        tracker = HistoricalTracker()
        tracker.display_learning_report()

    # League transfer statistics
    if show_league:
        from .activity_feed_learner import ActivityFeedLearner

        console.print("\n" + "═" * 60)
        feed_learner = ActivityFeedLearner()
        feed_learner.display_league_stats()
        console.print("[dim]Run 'rehoboam sync-activity-feed' to update this data[/dim]")

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
