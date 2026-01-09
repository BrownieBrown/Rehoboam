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
        max_trades_per_session: int = 5,  # Increased from 3 for more competitiveness
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

        # Learning system
        from .activity_feed_learner import ActivityFeedLearner
        from .bid_learner import BidLearner

        self.learner = BidLearner()
        self.activity_feed_learner = ActivityFeedLearner()

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
        # Pass learners to trader for adaptive bidding with competitive intelligence
        trader = Trader(
            self.api,
            self.settings,
            bid_learner=self.learner,
            activity_feed_learner=self.activity_feed_learner,
        )

        console.print("\n[bold cyan]🤖 Auto-Trading: Profit Opportunities[/bold cyan]")

        # Step 0: Check resolved auctions for learning
        self.check_resolved_auctions(league)

        # Step 0.5: Check bid compliance (league rule: no bids below market value)
        from .league_compliance import LeagueComplianceChecker

        compliance_checker = LeagueComplianceChecker(self.api, self.settings)

        # Get market data and trends for compliance checks
        market = self.api.get_market(league)
        kickbase_market = [p for p in market if p.is_kickbase_seller()]
        player_trends = trader._fetch_player_trends(kickbase_market, limit=50)

        # Run bid compliance check (adjust/cancel bids below market value)
        adjusted, canceled_compliance = compliance_checker.run_bid_compliance_check(
            league, player_trends=player_trends, auto_resolve=True, dry_run=self.dry_run
        )

        if adjusted > 0 or canceled_compliance > 0:
            console.print(
                f"\n[cyan]Bid compliance: {adjusted} adjusted, {canceled_compliance} canceled[/cyan]"
            )

        # Step 1: Re-evaluate active bids for quality
        from .bid_evaluator import BidEvaluator

        bid_evaluator = BidEvaluator(self.api, self.settings)

        bid_evaluations = bid_evaluator.evaluate_active_bids(
            league, player_trends=player_trends, for_profit=True
        )

        if bid_evaluations:
            bid_evaluator.display_bid_evaluations(bid_evaluations)

            # Cancel bad bids automatically
            canceled = bid_evaluator.cancel_bad_bids(league, bid_evaluations, dry_run=self.dry_run)
            if canceled > 0:
                console.print(
                    f"\n[yellow]Canceled {canceled} bid(s) that no longer make sense[/yellow]"
                )

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
                bid_increase_threshold = (
                    current_bid * 1.02
                )  # Need 2% higher to re-bid (more competitive)

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
        # Pass learners to trader for adaptive bidding with competitive intelligence
        trader = Trader(
            self.api,
            self.settings,
            bid_learner=self.learner,
            activity_feed_learner=self.activity_feed_learner,
        )

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

            # Record auction outcome for learning (bid placed)
            # Note: We don't know yet if we won or lost - that's determined later
            self._record_bid_placed(player, price)

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
            self.api.sell_player(league=league, player=player, price=price)

            console.print(
                f"[green]✓ {player.first_name} {player.last_name} listed for sale[/green]"
            )

            # Record flip outcome for learning (if we bought this player)
            self._record_flip_outcome(player, price)

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
                    trader = Trader(self.api, self.settings, bid_learner=self.learner)
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

        # Auto-sync activity feed for competitive intelligence
        try:
            console.print("\n[dim]Syncing league activity feed...[/dim]")
            activities = self.api.client.get_activities_feed(league.id, start=0)
            stats = self.activity_feed_learner.process_activity_feed(
                activities, api_client=self.api.client
            )

            if stats["transfers_new"] > 0 or stats["market_values_new"] > 0:
                console.print(
                    f"[dim]✓ Synced: {stats['transfers_new']} new transfers, {stats['market_values_new']} new market values[/dim]"
                )
        except Exception as e:
            console.print(f"[yellow]Warning: Could not sync activity feed: {e}[/yellow]")

        profit_results = []
        lineup_results = []
        errors = []

        # Step 0: Squad Optimization - Ensure best 11 and positive budget by gameday
        console.print("\n[bold cyan]🎯 Squad Optimization[/bold cyan]")
        try:
            squad_optimization = self.optimize_and_execute_squad(league)
            if squad_optimization and squad_optimization.players_to_sell:
                # Track sales as part of session results
                for player in squad_optimization.players_to_sell:
                    lineup_results.append(
                        AutoTradeResult(
                            success=True,
                            player_name=f"{player.first_name} {player.last_name}",
                            action="SELL",
                            price=player.market_value,
                            reason="Squad optimization - excess player",
                            timestamp=time.time(),
                        )
                    )
        except Exception as e:
            error_msg = f"Squad optimization error: {str(e)}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Run profit trading
        try:
            profit_results = self.run_profit_trading_session(league)
        except Exception as e:
            error_msg = f"Profit trading error: {str(e)}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Run lineup trading
        try:
            lineup_results.extend(self.run_lineup_improvement_session(league))
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

    def _record_bid_placed(self, player, our_bid: int):
        """Record that we placed a bid for learning"""
        try:
            from .bid_learner import AuctionOutcome
            from .value_calculator import PlayerValue

            # Calculate overbid percentage
            asking_price = player.price
            overbid_pct = ((our_bid - asking_price) / asking_price * 100) if asking_price > 0 else 0

            # Get value score
            value = PlayerValue.calculate(player)

            outcome = AuctionOutcome(
                player_id=player.id,
                player_name=f"{player.first_name} {player.last_name}",
                our_bid=our_bid,
                asking_price=asking_price,
                our_overbid_pct=overbid_pct,
                won=False,  # Will be updated when auction resolves
                timestamp=time.time(),
                player_value_score=value.value_score,
                market_value=player.market_value,
            )

            # Don't record yet - we'll record when we know the outcome
            # Store in a pending bids file for later resolution
            import json
            from pathlib import Path

            pending_file = Path("logs") / "pending_bids.json"
            pending_file.parent.mkdir(parents=True, exist_ok=True)

            # Load existing pending bids
            pending = []
            if pending_file.exists():
                with open(pending_file) as f:
                    pending = json.load(f)

            # Add new bid
            pending.append(
                {
                    "player_id": outcome.player_id,
                    "player_name": outcome.player_name,
                    "our_bid": outcome.our_bid,
                    "asking_price": outcome.asking_price,
                    "our_overbid_pct": outcome.our_overbid_pct,
                    "timestamp": outcome.timestamp,
                    "player_value_score": outcome.player_value_score,
                    "market_value": outcome.market_value,
                }
            )

            # Save
            with open(pending_file, "w") as f:
                json.dump(pending, f, indent=2)

        except Exception:
            pass  # Silent failure for bid recording

    def check_resolved_auctions(self, league):
        """Check pending bids and record outcomes for learning"""
        try:
            import json
            from pathlib import Path

            from .bid_learner import AuctionOutcome

            pending_file = Path("logs") / "pending_bids.json"
            if not pending_file.exists():
                return

            # Load pending bids
            with open(pending_file) as f:
                pending = json.load(f)

            if not pending:
                return

            # Get our current team and active bids
            my_team = self.api.get_squad(league)
            my_bids = self.api.get_my_bids(league)

            my_player_ids = {p.id for p in my_team}
            active_bid_ids = {p.id for p in my_bids}

            resolved = []
            still_pending = []

            for bid_data in pending:
                player_id = bid_data["player_id"]

                # Check if auction resolved
                if player_id in my_player_ids:
                    # We won!
                    outcome = AuctionOutcome(
                        player_id=bid_data["player_id"],
                        player_name=bid_data["player_name"],
                        our_bid=bid_data["our_bid"],
                        asking_price=bid_data["asking_price"],
                        our_overbid_pct=bid_data["our_overbid_pct"],
                        won=True,
                        timestamp=bid_data["timestamp"],
                        player_value_score=bid_data.get("player_value_score"),
                        market_value=bid_data.get("market_value"),
                    )
                    self.learner.record_outcome(outcome)
                    resolved.append(bid_data["player_name"])

                    # Also track purchase for flip outcome recording
                    self._track_purchase(player_id, bid_data)

                elif player_id not in active_bid_ids:
                    # Not in our team and not in active bids = we lost
                    outcome = AuctionOutcome(
                        player_id=bid_data["player_id"],
                        player_name=bid_data["player_name"],
                        our_bid=bid_data["our_bid"],
                        asking_price=bid_data["asking_price"],
                        our_overbid_pct=bid_data["our_overbid_pct"],
                        won=False,
                        timestamp=bid_data["timestamp"],
                        player_value_score=bid_data.get("player_value_score"),
                        market_value=bid_data.get("market_value"),
                    )
                    self.learner.record_outcome(outcome)
                    resolved.append(bid_data["player_name"])

                else:
                    # Still active bid
                    still_pending.append(bid_data)

            # Update pending file
            with open(pending_file, "w") as f:
                json.dump(still_pending, f, indent=2)

        except Exception:
            pass  # Silent failure for auction outcome recording

    def _track_purchase(self, player_id: str, bid_data: dict):
        """Track a player purchase for flip outcome recording"""
        try:
            import json
            from pathlib import Path

            purchases_file = Path("logs") / "tracked_purchases.json"
            purchases_file.parent.mkdir(parents=True, exist_ok=True)

            # Load existing purchases
            purchases = {}
            if purchases_file.exists():
                with open(purchases_file) as f:
                    purchases = json.load(f)

            # Add this purchase
            purchases[player_id] = {
                "player_name": bid_data["player_name"],
                "buy_price": bid_data["our_bid"],
                "buy_date": bid_data["timestamp"],
            }

            # Save
            with open(purchases_file, "w") as f:
                json.dump(purchases, f, indent=2)

        except Exception:
            pass  # Silent failure for purchase tracking

    def optimize_and_execute_squad(self, league):
        """
        Run squad optimization and execute recommended sales

        Returns:
            SquadOptimization result
        """
        from .squad_optimizer import SquadOptimizer
        from .trader import Trader

        # Use trader's full optimization method (includes all context: performance, SOS, matchups)
        trader = Trader(self.api, self.settings, bid_learner=self.learner)
        optimization = trader.optimize_squad_for_gameday(league)

        if not optimization:
            return None

        # Get player values for display
        team_analyses = trader.analyze_team(league)
        player_values = trader.get_player_values_from_analyses(team_analyses)

        # Display optimization
        optimizer = SquadOptimizer(min_squad_size=11, max_squad_size=15)
        optimizer.display_optimization(optimization, player_values=player_values)

        # Execute recommended sales if needed
        if optimization.players_to_sell and not optimization.is_gameday_ready:
            console.print(
                f"\n[yellow]⚠️  Budget negative, selling {len(optimization.players_to_sell)} player(s)...[/yellow]"
            )
            results = optimizer.execute_sell_recommendations(
                optimization, api=self.api, league=league, dry_run=self.dry_run
            )

            if results["sold"]:
                console.print(
                    f"[green]✓ Sold {len(results['sold'])} player(s) for €{sum(p.market_value for p in results['sold']):,}[/green]"
                )

        return optimization

    def _record_flip_outcome(self, player, sell_price: int):
        """Record a flip outcome when we sell a player we bought"""
        try:
            import json
            from pathlib import Path

            from .bid_learner import FlipOutcome

            purchases_file = Path("logs") / "tracked_purchases.json"
            if not purchases_file.exists():
                return

            # Load purchases
            with open(purchases_file) as f:
                purchases = json.load(f)

            if player.id not in purchases:
                return  # We didn't buy this player (or not tracked)

            purchase_data = purchases[player.id]
            buy_price = purchase_data["buy_price"]
            buy_date = purchase_data["buy_date"]
            sell_date = time.time()

            profit = sell_price - buy_price
            profit_pct = (profit / buy_price * 100) if buy_price > 0 else 0
            hold_days = int((sell_date - buy_date) / (24 * 3600))

            outcome = FlipOutcome(
                player_id=player.id,
                player_name=f"{player.first_name} {player.last_name}",
                buy_price=buy_price,
                sell_price=sell_price,
                profit=profit,
                profit_pct=profit_pct,
                hold_days=hold_days,
                buy_date=buy_date,
                sell_date=sell_date,
                average_points=getattr(player, "average_points", None),
                position=getattr(player, "position", None),
                was_injured=(player.status != 0) if hasattr(player, "status") else False,
            )

            self.learner.record_flip(outcome)

            # Remove from purchases
            del purchases[player.id]
            with open(purchases_file, "w") as f:
                json.dump(purchases, f, indent=2)

        except Exception:
            pass  # Silent failure for flip outcome recording
