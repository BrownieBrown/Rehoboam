"""Automated trading - Execute trades without manual intervention"""

import time
from dataclasses import dataclass
from datetime import datetime

from rich.console import Console

console = Console()


@dataclass
class AutoTradeResult:
    """Result of an automated trade"""

    success: bool
    player_name: str
    action: str  # BUY, SELL
    price: int
    reason: str
    timestamp: float
    error: str | None = None


@dataclass
class AutoTradeSession:
    """Summary of an automated trading session"""

    start_time: float
    end_time: float
    profit_trades: list[AutoTradeResult]
    lineup_trades: list[AutoTradeResult]
    errors: list[str]
    total_spent: int
    total_earned: int
    net_change: int


class AutoTrader:
    """Executes trades automatically based on bot recommendations"""

    def __init__(
        self,
        api,
        settings,
        max_trades_per_session: int = 3,
        max_daily_spend: int = 50_000_000,  # 50M max per day
        dry_run: bool = False,
    ):
        """
        Args:
            api: KickbaseAPI instance
            settings: Bot settings
            max_trades_per_session: Max trades per run (safety limit)
            max_daily_spend: Max money to spend per day (safety limit)
            dry_run: If True, simulate but don't execute
        """
        self.api = api
        self.settings = settings
        self.max_trades_per_session = max_trades_per_session
        self.max_daily_spend = max_daily_spend
        self.dry_run = dry_run

        # Daily tracking
        self.daily_spend = 0
        self.last_reset = datetime.now().date()

    def _reset_daily_limits_if_needed(self):
        """Reset daily limits at midnight"""
        today = datetime.now().date()
        if today > self.last_reset:
            self.daily_spend = 0
            self.last_reset = today
            console.print("[cyan]Daily limits reset[/cyan]")

    def run_profit_trading_session(self, league) -> list[AutoTradeResult]:
        """
        Execute profit trading opportunities automatically

        Returns:
            List of AutoTradeResult
        """
        from .trader import Trader

        results = []
        trader = Trader(self.api, self.settings)

        console.print("\n[bold cyan]🤖 Auto-Trading: Profit Opportunities[/bold cyan]")

        # Check daily limits
        self._reset_daily_limits_if_needed()
        if self.daily_spend >= self.max_daily_spend:
            console.print(f"[yellow]Daily spend limit reached (€{self.max_daily_spend:,})[/yellow]")
            return results

        # Find opportunities
        opportunities = trader.find_profit_opportunities(league)

        if not opportunities:
            console.print("[dim]No profit opportunities found[/dim]")
            return results

        # Check active bids - we can have multiple bids simultaneously
        my_bids = self.api.get_my_bids(league)

        # Build map of player_id -> our bid amount
        my_bid_amounts = {p.id: p.user_offer_price for p in my_bids}

        if my_bids:
            console.print(f"[cyan]📊 Active bids: {len(my_bids)}[/cyan]")
            for bid_player in my_bids:
                console.print(
                    f"  - {bid_player.first_name} {bid_player.last_name}: Your bid €{bid_player.user_offer_price:,}"
                )
            console.print("[dim]Note: You'll find out who won when auctions end[/dim]")

        # Get current budget and team value for debt capacity
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        team_value = team_info.get("team_value", 0)

        # Calculate debt capacity for profit flipping
        max_debt_pct = self.settings.max_debt_pct_of_team_value
        max_debt = int(team_value * (max_debt_pct / 100))

        # For profit flipping, we can use debt capacity (will sell before match day)
        pending_bid_total = sum(p.user_offer_price for p in my_bids)
        flip_budget = current_budget + max_debt - pending_bid_total

        console.print(f"[dim]Found {len(opportunities)} opportunities[/dim]")
        console.print(f"[dim]Current budget: €{current_budget:,}[/dim]")
        console.print(f"[dim]Team value: €{team_value:,}[/dim]")
        console.print(f"[dim]Max debt %: {max_debt_pct}%[/dim]")
        if pending_bid_total > 0:
            console.print(f"[dim]Pending bids: €{pending_bid_total:,}[/dim]")
        console.print(f"[dim]Debt capacity: €{max_debt:,}[/dim]")
        console.print(f"[dim]Available for flips: €{flip_budget:,}[/dim]")

        # Execute top opportunities (up to max_trades_per_session)
        executed_count = 0
        for opp in opportunities:
            if executed_count >= self.max_trades_per_session:
                console.print(
                    f"[yellow]Max trades per session reached ({self.max_trades_per_session})[/yellow]"
                )
                break

            if self.daily_spend + opp.buy_price >= self.max_daily_spend:
                console.print("[yellow]Would exceed daily spend limit, stopping[/yellow]")
                break

            # Check if we already have a bid on this player
            current_bid = my_bid_amounts.get(opp.player.id, 0)

            if current_bid > 0:
                # We already bid on this player - should we increase our bid?
                bid_increase_threshold = current_bid * 1.05  # Need 5% higher to re-bid

                if opp.buy_price <= current_bid:
                    console.print(
                        f"[dim]Skipping {opp.player.first_name} {opp.player.last_name} - already bid €{current_bid:,}[/dim]"
                    )
                    continue
                elif opp.buy_price < bid_increase_threshold:
                    console.print(
                        f"[dim]Skipping {opp.player.first_name} {opp.player.last_name} - already bid €{current_bid:,}, new bid €{opp.buy_price:,} not enough higher[/dim]"
                    )
                    continue
                else:
                    console.print(
                        f"[yellow]⚠ {opp.player.first_name} {opp.player.last_name} - increasing bid €{current_bid:,} → €{opp.buy_price:,}[/yellow]"
                    )

            # Check if affordable (use flip budget with debt capacity)
            if opp.buy_price > flip_budget:
                console.print(
                    f"[yellow]Cannot afford {opp.player.first_name} {opp.player.last_name} (€{opp.buy_price:,})[/yellow]"
                )
                continue

            # Execute trade
            result = self._execute_buy(league, opp)
            results.append(result)

            if result.success:
                executed_count += 1
                self.daily_spend += opp.buy_price
                flip_budget -= opp.buy_price

        console.print(f"\n[green]✓ Executed {executed_count} profit trades[/green]")
        return results

    def run_lineup_improvement_session(self, league) -> list[AutoTradeResult]:
        """
        Execute lineup improvement trades automatically

        Returns:
            List of AutoTradeResult
        """
        from .trader import Trader

        results = []
        trader = Trader(self.api, self.settings)

        console.print("\n[bold cyan]🤖 Auto-Trading: Lineup Improvements[/bold cyan]")

        # Check daily limits
        self._reset_daily_limits_if_needed()
        if self.daily_spend >= self.max_daily_spend:
            console.print(f"[yellow]Daily spend limit reached (€{self.max_daily_spend:,})[/yellow]")
            return results

        # Find trades
        trades = trader.find_trade_opportunities(league)

        if not trades:
            console.print("[dim]No lineup improvement trades found[/dim]")
            return results

        # Check active bids - we can have multiple bids simultaneously
        my_bids = self.api.get_my_bids(league)

        # Build map of player_id -> our bid amount
        my_bid_amounts = {p.id: p.user_offer_price for p in my_bids}

        if my_bids:
            console.print(f"[cyan]📊 Active bids: {len(my_bids)}[/cyan]")

        # Get current budget
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        team_value = team_info.get("team_value", 0)
        max_debt = int(team_value * (self.settings.max_debt_pct_of_team_value / 100))

        # Calculate effective budget (subtract pending bids)
        pending_bid_total = sum(p.user_offer_price for p in my_bids)
        available_budget = current_budget + max_debt - pending_bid_total

        console.print(f"[dim]Found {len(trades)} lineup trades[/dim]")
        if pending_bid_total > 0:
            console.print(
                f"[dim]Budget: €{current_budget:,}, Pending bids: €{pending_bid_total:,}[/dim]"
            )
        console.print(f"[dim]Available budget (with debt): €{available_budget:,}[/dim]")

        # Execute best trade (only 1 lineup trade per session for safety)
        for trade in trades[:1]:  # Only top trade
            if self.daily_spend + trade.required_budget >= self.max_daily_spend:
                console.print("[yellow]Would exceed daily spend limit, skipping[/yellow]")
                break

            # Show if trade includes players we already have bids on
            players_with_bids = [p for p in trade.players_in if p.id in my_bid_amounts]
            if players_with_bids:
                console.print(
                    f"[yellow]⚠ Trade includes players you bid on: {', '.join(p.first_name + ' ' + p.last_name for p in players_with_bids)}[/yellow]"
                )
                console.print(
                    "[dim]Bot will attempt trade anyway - you can have multiple bids[/dim]"
                )

            # Check if affordable
            if trade.required_budget > available_budget:
                console.print(
                    f"[yellow]Cannot afford trade (needs €{trade.required_budget:,})[/yellow]"
                )
                continue

            # Execute trade (buy all, then sell all)
            trade_results = self._execute_lineup_trade(league, trade)
            results.extend(trade_results)

            if all(r.success for r in trade_results):
                self.daily_spend += trade.required_budget
                console.print("\n[green]✓ Executed lineup improvement trade[/green]")
            else:
                console.print("\n[red]✗ Lineup trade partially failed[/red]")

            break  # Only one lineup trade per session

        return results

    def _execute_buy(self, league, opportunity) -> AutoTradeResult:
        """Execute a buy order"""
        player = opportunity.player
        price = opportunity.buy_price

        console.print(
            f"\n[cyan]Buying {player.first_name} {player.last_name} for €{price:,}[/cyan]"
        )
        console.print(f"[dim]Reason: {opportunity.reason}[/dim]")

        if self.dry_run:
            console.print("[yellow]DRY RUN: Trade not executed[/yellow]")
            return AutoTradeResult(
                success=True,
                player_name=f"{player.first_name} {player.last_name}",
                action="BUY",
                price=price,
                reason=opportunity.reason,
                timestamp=time.time(),
            )

        try:
            # Place offer
            self.api.buy_player(league, player, price)

            console.print(
                f"[green]✓ Buy order placed for {player.first_name} {player.last_name}[/green]"
            )

            return AutoTradeResult(
                success=True,
                player_name=f"{player.first_name} {player.last_name}",
                action="BUY",
                price=price,
                reason=opportunity.reason,
                timestamp=time.time(),
            )

        except Exception as e:
            error_msg = str(e)
            console.print(
                f"[red]✗ Failed to buy {player.first_name} {player.last_name}: {error_msg}[/red]"
            )

            return AutoTradeResult(
                success=False,
                player_name=f"{player.first_name} {player.last_name}",
                action="BUY",
                price=price,
                reason=opportunity.reason,
                timestamp=time.time(),
                error=error_msg,
            )

    def _execute_sell(self, league, player, price: int, reason: str) -> AutoTradeResult:
        """Execute a sell order"""
        console.print(
            f"\n[cyan]Selling {player.first_name} {player.last_name} for €{price:,}[/cyan]"
        )
        console.print(f"[dim]Reason: {reason}[/dim]")

        if self.dry_run:
            console.print("[yellow]DRY RUN: Trade not executed[/yellow]")
            return AutoTradeResult(
                success=True,
                player_name=f"{player.first_name} {player.last_name}",
                action="SELL",
                price=price,
                reason=reason,
                timestamp=time.time(),
            )

        try:
            # List player on market
            self.api.list_player(league_id=league.id, player_id=player.id, price=price)

            console.print(
                f"[green]✓ {player.first_name} {player.last_name} listed for sale[/green]"
            )

            return AutoTradeResult(
                success=True,
                player_name=f"{player.first_name} {player.last_name}",
                action="SELL",
                price=price,
                reason=reason,
                timestamp=time.time(),
            )

        except Exception as e:
            error_msg = str(e)
            console.print(
                f"[red]✗ Failed to sell {player.first_name} {player.last_name}: {error_msg}[/red]"
            )

            return AutoTradeResult(
                success=False,
                player_name=f"{player.first_name} {player.last_name}",
                action="SELL",
                price=price,
                reason=reason,
                timestamp=time.time(),
                error=error_msg,
            )

    def _execute_lineup_trade(self, league, trade) -> list[AutoTradeResult]:
        """Execute a lineup improvement trade (N-for-M) with dynamic bid refresh"""
        results = []

        console.print(
            f"\n[cyan]Executing {len(trade.players_in)}-for-{len(trade.players_out)} lineup trade[/cyan]"
        )
        console.print(f"[dim]Expected improvement: +{trade.improvement_points:.1f} pts/week[/dim]")

        # Step 1: Buy all players first
        console.print("\n[cyan]Step 1: Buying players...[/cyan]")
        for player in trade.players_in:
            # Dynamic bid refresh: recalculate smart bid before buying
            original_bid = (
                trade.smart_bids.get(player.id) if trade.smart_bids else player.market_value
            )

            # Refresh player data to get current market value
            try:
                from .trader import Trader
                from .value_calculator import PlayerValue

                # Get fresh market data
                market_players = self.api.get_market(league)
                fresh_player = next((p for p in market_players if p.id == player.id), None)

                if fresh_player and fresh_player.market_value != player.market_value:
                    console.print(
                        f"[yellow]⚠ Market value changed: €{player.market_value:,} → €{fresh_player.market_value:,}[/yellow]"
                    )

                    # Recalculate smart bid with fresh market value
                    trader = Trader(self.api, self.settings)
                    value = PlayerValue.calculate(fresh_player)
                    fresh_bid = trader.bidding.calculate_bid(
                        asking_price=fresh_player.price,
                        market_value=fresh_player.market_value,
                        value_score=value.value_score,
                        confidence=0.8,
                    )

                    buy_price = fresh_bid.recommended_bid
                    price_increase_pct = (
                        ((buy_price - original_bid) / original_bid) * 100 if original_bid else 0
                    )

                    if price_increase_pct > 10:
                        console.print(
                            f"[yellow]⚠ Bid increased by {price_increase_pct:.1f}% (€{original_bid:,} → €{buy_price:,})[/yellow]"
                        )

                    # Update player reference for buying
                    player = fresh_player
                else:
                    buy_price = original_bid

            except Exception as e:
                console.print(f"[yellow]⚠ Could not refresh bid (using original): {e}[/yellow]")
                buy_price = original_bid

            result = self._execute_buy(
                league,
                type(
                    "Opp",
                    (),
                    {
                        "player": player,
                        "buy_price": buy_price,
                        "reason": f"Lineup improvement (+{trade.improvement_points:.1f} pts)",
                    },
                )(),
            )
            results.append(result)

            if not result.success:
                console.print("[red]Buy failed, aborting trade[/red]")
                return results

            # Wait between buys
            time.sleep(2)

        # Step 2: Sell all players
        console.print("\n[cyan]Step 2: Selling players...[/cyan]")
        for player in trade.players_out:
            result = self._execute_sell(
                league, player, player.market_value, "Lineup improvement (replaced)"
            )
            results.append(result)

            # Wait between sells
            time.sleep(2)

        return results

    def run_full_session(self, league) -> AutoTradeSession:
        """
        Run a complete automated trading session

        Returns:
            AutoTradeSession with summary
        """
        start_time = time.time()

        console.print(f"\n{'=' * 70}")
        console.print(
            f"[bold]Automated Trading Session - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]"
        )
        if self.dry_run:
            console.print("[yellow]DRY RUN MODE - No trades will be executed[/yellow]")
        console.print(f"{'=' * 70}")

        profit_results = []
        lineup_results = []
        errors = []

        # Run profit trading
        try:
            profit_results = self.run_profit_trading_session(league)
        except Exception as e:
            error_msg = f"Profit trading error: {str(e)}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Run lineup trading
        try:
            lineup_results = self.run_lineup_improvement_session(league)
        except Exception as e:
            error_msg = f"Lineup trading error: {str(e)}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Calculate totals
        total_spent = sum(
            r.price for r in profit_results + lineup_results if r.action == "BUY" and r.success
        )
        total_earned = sum(
            r.price for r in profit_results + lineup_results if r.action == "SELL" and r.success
        )
        net_change = total_earned - total_spent

        end_time = time.time()

        # Print summary
        console.print(f"\n{'=' * 70}")
        console.print("[bold]Session Summary[/bold]")
        console.print(f"{'=' * 70}")
        console.print(f"Duration: {end_time - start_time:.1f}s")
        console.print(
            f"Profit trades: {len([r for r in profit_results if r.success])}/{len(profit_results)}"
        )
        console.print(
            f"Lineup trades: {len([r for r in lineup_results if r.success])}/{len(lineup_results)}"
        )
        console.print(f"Total spent: €{total_spent:,}")
        console.print(f"Total earned: €{total_earned:,}")
        net_color = "green" if net_change >= 0 else "red"
        console.print(f"Net change: [{net_color}]€{net_change:,}[/{net_color}]")

        if errors:
            console.print(f"\n[red]Errors: {len(errors)}[/red]")
            for err in errors:
                console.print(f"[red]  • {err}[/red]")

        return AutoTradeSession(
            start_time=start_time,
            end_time=end_time,
            profit_trades=profit_results,
            lineup_trades=lineup_results,
            errors=errors,
            total_spent=total_spent,
            total_earned=total_earned,
            net_change=net_change,
        )
