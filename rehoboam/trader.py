"""Trading logic and automation"""

from rich.console import Console
from rich.table import Table
from .kickbase_client import League

from .api import KickbaseAPI
from .analyzer import MarketAnalyzer, PlayerAnalysis
from .config import Settings

console = Console()


class Trader:
    """Handles automated trading operations"""

    def __init__(self, api: KickbaseAPI, settings: Settings):
        self.api = api
        self.settings = settings
        self.analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=settings.min_buy_value_increase_pct,
            min_sell_profit_pct=settings.min_sell_profit_pct,
            max_loss_pct=settings.max_loss_pct,
        )

    def analyze_market(self, league: League) -> list[PlayerAnalysis]:
        """Analyze all players on the market"""
        console.print("[cyan]Fetching market data...[/cyan]")
        market_players = self.api.get_market(league)

        # Filter for only KICKBASE sellers (not user listings)
        kickbase_players = [p for p in market_players if p.is_kickbase_seller()]
        user_listings = len(market_players) - len(kickbase_players)

        console.print(f"[green]Found {len(kickbase_players)} KICKBASE players on the market[/green]")
        if user_listings > 0:
            console.print(f"[dim](Filtered out {user_listings} user listings)[/dim]")

        analyses = []
        for player in kickbase_players:
            analysis = self.analyzer.analyze_market_player(player)
            analyses.append(analysis)

        return analyses

    def analyze_team(self, league: League) -> list[PlayerAnalysis]:
        """Analyze all players in your team"""
        console.print("[cyan]Fetching your squad...[/cyan]")
        players = self.api.get_squad(league)

        # Get starting eleven to protect them from being sold
        starting_eleven_ids = set()
        if self.settings.never_sell_starters:
            try:
                starting_eleven = self.api.get_starting_eleven(league)
                # /teamcenter/myeleven returns starting 11 players in 'lp' field
                player_list = starting_eleven.get("lp", [])

                if isinstance(player_list, list):
                    starting_eleven_ids = {p.get("i", "") for p in player_list if isinstance(p, dict)}
                    console.print(f"[yellow]Protecting {len(starting_eleven_ids)} starters from being sold[/yellow]")
                else:
                    console.print(f"[yellow]Warning: Starting eleven data format unexpected[/yellow]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not fetch starting eleven: {e}[/yellow]")

        console.print(f"[green]You have {len(players)} players in squad[/green]")

        # Check minimum squad size
        if len(players) <= self.settings.min_squad_size:
            console.print(f"[red]Warning: Squad at minimum size ({len(players)}/{self.settings.min_squad_size}). Cannot sell any players![/red]")

        analyses = []
        for player in players:
            # Check if this player should be protected
            is_starter = player.id in starting_eleven_ids
            is_high_performer = player.points >= self.settings.min_points_to_keep

            # Analyze the player
            analysis = self.analyzer.analyze_owned_player(player)

            # Override SELL recommendation if player should be protected
            if analysis.recommendation == "SELL":
                if len(players) <= self.settings.min_squad_size:
                    analysis.recommendation = "HOLD"
                    analysis.reason = f"Squad at minimum size - cannot sell"
                elif is_starter:
                    analysis.recommendation = "HOLD"
                    analysis.reason = f"Starter - don't sell (original: {analysis.reason})"
                elif is_high_performer:
                    analysis.recommendation = "HOLD"
                    analysis.reason = f"High performer ({player.points} pts) - don't sell"

            analyses.append(analysis)

        return analyses

    def display_analysis(self, analyses: list[PlayerAnalysis], title: str = "Analysis"):
        """Display analysis results in a nice table"""
        table = Table(title=title, show_header=True, header_style="bold magenta")
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Position", style="blue")
        table.add_column("Price", justify="right", style="yellow")
        table.add_column("Value Score", justify="right", style="magenta")
        table.add_column("Pts/M€", justify="right", style="green")
        table.add_column("Points", justify="right", style="green")
        table.add_column("Recommendation", justify="center")
        table.add_column("Reason", style="dim")

        for analysis in analyses:
            player = analysis.player
            name = f"{player.first_name} {player.last_name}"

            # Color code value score
            if analysis.value_score >= 60:
                score_color = "green"
            elif analysis.value_score >= 40:
                score_color = "yellow"
            else:
                score_color = "red"
            score_str = f"[{score_color}]{analysis.value_score:.1f}[/{score_color}]"

            # Color code recommendation
            rec_color = {
                "BUY": "green",
                "SELL": "red",
                "HOLD": "yellow",
                "SKIP": "dim",
            }.get(analysis.recommendation, "white")
            rec_str = f"[{rec_color}]{analysis.recommendation}[/{rec_color}]"

            table.add_row(
                name,
                player.position,
                f"€{analysis.current_price:,}",
                score_str,
                f"{analysis.points_per_million:.1f}",
                str(analysis.points),
                rec_str,
                analysis.reason,
            )

        console.print(table)

    def execute_trades(
        self, league: League, buy_analyses: list[PlayerAnalysis]
    ) -> dict:
        """Execute recommended trades"""
        results = {"bought": [], "failed": [], "skipped": []}

        if not buy_analyses:
            console.print("[yellow]No trading opportunities found[/yellow]")
            return results

        # Get current budget
        team_info = self.api.get_team_info(league)
        budget = team_info["budget"]
        available_budget = budget - self.settings.reserve_budget

        console.print(f"\n[cyan]Budget: €{budget:,}[/cyan]")
        console.print(f"[cyan]Available for trading: €{available_budget:,}[/cyan]\n")

        for analysis in buy_analyses:
            player = analysis.player
            price = analysis.current_price

            # Check budget constraints
            if price > available_budget:
                console.print(
                    f"[yellow]Skipping {player.first_name} {player.last_name}: "
                    f"Insufficient budget (€{price:,} > €{available_budget:,})[/yellow]"
                )
                results["skipped"].append(analysis)
                continue

            if price > self.settings.max_player_cost:
                console.print(
                    f"[yellow]Skipping {player.first_name} {player.last_name}: "
                    f"Exceeds max player cost (€{price:,} > €{self.settings.max_player_cost:,})[/yellow]"
                )
                results["skipped"].append(analysis)
                continue

            # Execute trade
            if self.settings.dry_run:
                console.print(
                    f"[blue][DRY RUN] Would make offer for {player.first_name} {player.last_name} "
                    f"at €{price:,}[/blue]"
                )
                results["bought"].append(analysis)
                available_budget -= price  # Simulate budget reduction
            else:
                try:
                    self.api.buy_player(league, player, price)
                    console.print(
                        f"[green]✓ Made offer for {player.first_name} {player.last_name} "
                        f"at €{price:,}[/green]"
                    )
                    results["bought"].append(analysis)
                    available_budget -= price
                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to make offer for {player.first_name} {player.last_name}: {e}[/red]"
                    )
                    results["failed"].append(analysis)

        return results

    def auto_trade(self, league: League, max_trades: int = 5):
        """Run automated trading cycle"""
        console.print("\n[bold cyan]Starting Automated Trading Cycle[/bold cyan]\n")

        if self.settings.dry_run:
            console.print("[yellow]⚠️  DRY RUN MODE - No real trades will be executed[/yellow]\n")

        # Analyze market
        market_analyses = self.analyze_market(league)

        # Find best opportunities
        opportunities = self.analyzer.find_best_opportunities(market_analyses, top_n=max_trades)

        if opportunities:
            console.print(f"\n[green]Found {len(opportunities)} trading opportunities![/green]\n")
            self.display_analysis(opportunities, title="Top Trading Opportunities")

            # Execute trades
            results = self.execute_trades(league, opportunities)

            # Summary
            console.print("\n[bold]Trading Summary:[/bold]")
            console.print(f"  Bought: {len(results['bought'])}")
            console.print(f"  Failed: {len(results['failed'])}")
            console.print(f"  Skipped: {len(results['skipped'])}")
        else:
            console.print("[yellow]No trading opportunities found at this time[/yellow]")
