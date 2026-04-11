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


@dataclass
class MatchdayPhase:
    """Trading aggressiveness based on how close the next match is."""

    days_until_match: int | None  # None = unknown
    phase: str  # "aggressive" | "moderate" | "locked"
    max_trades: int
    allow_flips: bool  # Profit flips only make sense with enough time to sell
    reason: str


@dataclass
class EPSessionContext:
    """Single-fetch context for the entire auto session."""

    ep_result: dict
    matchday_phase: MatchdayPhase
    my_bids: list
    my_bid_amounts: dict  # {player_id: bid_amount}
    squad: list
    current_budget: int
    team_value: int
    flip_budget: int
    executed_trade_count: int = 0


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

    def get_quick_budget(self, league) -> int | None:
        """Lightweight budget check using the budget-only endpoint."""
        try:
            data = self.api.get_budget(league)
            return data.get("b", data.get("budget", None))
        except Exception:
            return None

    def _reset_daily_limits_if_needed(self):
        """Reset daily limits at midnight"""
        today = datetime.now().date()
        if today > self.last_reset:
            self.daily_spend = 0
            self.last_reset = today
            console.print("[cyan]Daily limits reset[/cyan]")

    def _get_matchday_phase(self, days_until_match: int | None) -> MatchdayPhase:
        """Determine trading aggressiveness based on days to next match."""
        if days_until_match is not None and days_until_match <= 1:
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="locked",
                max_trades=0,
                allow_flips=False,
                reason=f"Match in {days_until_match}d — lineup only, no trading",
            )
        elif days_until_match is not None and days_until_match <= 4:
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="moderate",
                max_trades=max(self.max_trades_per_session // 2, 2),
                allow_flips=False,
                reason=f"Match in {days_until_match}d — lineup improvements only",
            )
        else:
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="aggressive",
                max_trades=self.max_trades_per_session,
                allow_flips=True,
                reason=(
                    f"Match in {days_until_match}d — full trading"
                    if days_until_match
                    else "Unknown schedule — full trading"
                ),
            )

    def _build_session_context(self, league) -> EPSessionContext:
        """Build the single-fetch context for the entire session."""
        from .trader import Trader

        trader = Trader(
            self.api,
            self.settings,
            bid_learner=self.learner,
            activity_feed_learner=self.activity_feed_learner,
        )

        # Fetch matchday timing
        days = trader.get_days_until_match(league)
        phase = self._get_matchday_phase(days)

        console.print(f"[cyan]📅 {phase.reason}[/cyan]")

        # Single EP pipeline call with trend data
        ep_result = trader.get_ep_recommendations_with_trends(league)

        # Fetch bids and squad
        my_bids = self.api.get_my_bids(league)
        squad = self.api.get_squad(league)
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        team_value = team_info.get("team_value", 0)

        # Calculate flip budget based on matchday phase
        max_debt = int(team_value * (self.settings.max_debt_pct_of_team_value / 100))
        pending_bid_total = sum(p.user_offer_price for p in my_bids)

        if phase.phase == "locked":
            flip_budget = 0
        elif phase.phase == "moderate":
            flip_budget = current_budget - pending_bid_total
        else:
            flip_budget = current_budget + max_debt - pending_bid_total

        return EPSessionContext(
            ep_result=ep_result,
            matchday_phase=phase,
            my_bids=my_bids,
            my_bid_amounts={p.id: p.user_offer_price for p in my_bids},
            squad=squad,
            current_budget=current_budget,
            team_value=team_value,
            flip_budget=flip_budget,
        )

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

    def _execute_instant_sell(self, league, player, reason: str) -> AutoTradeResult:
        """Sell a player instantly to Kickbase at market value.

        Unlike _execute_sell (which lists on the transfer market),
        this immediately removes the player from the squad and frees the slot.
        Used by trade pairs where the slot must be free before placing a buy bid.
        """
        price = player.market_value
        console.print(
            f"\n[cyan]Instant-selling {player.first_name} {player.last_name}"
            f" to Kickbase for ~€{price:,}[/cyan]"
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
            self.api.sell_player_instant(league=league, player=player)

            console.print(
                f"[green]✓ {player.first_name} {player.last_name} sold instantly to Kickbase[/green]"
            )

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
                f"[red]✗ Failed to instant-sell {player.first_name} {player.last_name}: {error_msg}[/red]"
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

    def run_unified_trade_phase(self, league, ctx: EPSessionContext) -> list[AutoTradeResult]:
        """Execute all qualifying trades from a single ranked candidate list.

        Trade pairs and plain buys compete head-to-head by EP gain.
        This replaces the old separate profit + lineup sessions.
        """
        from types import SimpleNamespace

        results: list[AutoTradeResult] = []
        buy_recs = ctx.ep_result.get("buy_recs", [])
        trade_pairs = ctx.ep_result.get("trade_pairs", [])

        effective_limit = min(
            self.max_trades_per_session,
            ctx.matchday_phase.max_trades,
        )

        console.print(
            f"\n[bold cyan]🤖 Unified Trade Phase "
            f"(limit {effective_limit}, phase: {ctx.matchday_phase.phase})[/bold cyan]"
        )

        # Build unified candidate list: (kind, ep_value, object)
        candidates = []
        for rec in buy_recs:
            if rec.recommended_bid and rec.recommended_bid > 0:
                candidates.append(("buy", rec.marginal_ep_gain, rec))
        for pair in trade_pairs:
            if pair.recommended_bid and pair.recommended_bid > 0:
                candidates.append(("pair", pair.ep_gain, pair))

        # Sort by EP gain descending — trade pairs compete directly with plain buys
        candidates.sort(key=lambda x: x[1], reverse=True)

        if not candidates:
            console.print("[dim]No actionable opportunities[/dim]")
            return results

        console.print(
            f"[cyan]📋 {len(candidates)} candidates "
            f"({sum(1 for c in candidates if c[0] == 'buy')} buys, "
            f"{sum(1 for c in candidates if c[0] == 'pair')} trade pairs)[/cyan]"
        )

        # Refresh squad and bids — earlier phases (sell monitoring, squad optimization)
        # may have changed the actual counts since ctx was built.
        fresh_squad = self.api.get_squad(league)
        fresh_bids = self.api.get_my_bids(league)
        current_squad_size = len(fresh_squad)
        active_bid_count = len(fresh_bids)
        available_slots = 15 - current_squad_size - active_bid_count
        # Update bid amounts map for duplicate-bid detection
        ctx.my_bid_amounts = {p.id: p.user_offer_price for p in fresh_bids}

        console.print(
            f"[cyan]📋 Squad: {current_squad_size} + {active_bid_count} bids = "
            f"{current_squad_size + active_bid_count}/15 "
            f"({available_slots} slot(s) open)[/cyan]"
        )

        # Also add profit flip candidates if phase allows and there are open slots
        profit_flip_candidates = []
        if ctx.matchday_phase.allow_flips and available_slots > 0:
            try:
                from .trader import Trader

                trader = Trader(
                    self.api,
                    self.settings,
                    bid_learner=self.learner,
                    activity_feed_learner=self.activity_feed_learner,
                )
                profit_opps = trader.find_profit_opportunities(league)
                # Filter out players already in EP candidates
                ep_player_ids = {
                    rec.player.id for _, _, rec in candidates if hasattr(rec, "player")
                } | {pair.buy_player.id for _, _, pair in candidates if hasattr(pair, "buy_player")}
                for opp in profit_opps:
                    if opp.player.id not in ep_player_ids:
                        profit_flip_candidates.append(opp)
                if profit_flip_candidates:
                    console.print(
                        f"[cyan]💰 + {len(profit_flip_candidates)} profit flip candidate(s)[/cyan]"
                    )
            except Exception as e:
                console.print(f"[yellow]Profit flip search failed: {e}[/yellow]")

        for kind, _ep_val, obj in candidates:
            if ctx.executed_trade_count >= effective_limit:
                console.print(f"[yellow]Trade limit reached ({effective_limit})[/yellow]")
                break
            if self.daily_spend >= self.max_daily_spend:
                console.print("[yellow]Daily spend limit reached[/yellow]")
                break

            if kind == "buy":
                if available_slots <= 0:
                    continue  # No slot for a plain buy
                if ctx.my_bid_amounts.get(obj.player.id, 0) > 0:
                    console.print(
                        f"[dim]Skip {obj.player.last_name} — already have active bid[/dim]"
                    )
                    continue
                if obj.recommended_bid > ctx.flip_budget:
                    console.print(
                        f"[yellow]Cannot afford {obj.player.last_name} "
                        f"(€{obj.recommended_bid:,} > €{ctx.flip_budget:,})[/yellow]"
                    )
                    continue

                result = self._execute_buy(
                    league,
                    SimpleNamespace(
                        player=obj.player,
                        buy_price=obj.recommended_bid,
                        reason=obj.reason,
                    ),
                )
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    ctx.flip_budget -= obj.recommended_bid
                    available_slots -= 1

            elif kind == "pair":
                # Don't sell a player unnecessarily if there are open slots —
                # the same target should appear as a plain buy candidate instead.
                if available_slots > 0:
                    continue
                if ctx.my_bid_amounts.get(obj.buy_player.id, 0) > 0:
                    console.print(
                        f"[dim]Skip pair {obj.buy_player.last_name} — already have active bid[/dim]"
                    )
                    continue
                net_cost = obj.recommended_bid - int(obj.sell_player.market_value * 0.95)
                if net_cost > ctx.flip_budget:
                    console.print(
                        f"[yellow]Cannot afford trade pair "
                        f"{obj.sell_player.last_name}→{obj.buy_player.last_name} "
                        f"(net €{net_cost:,} > €{ctx.flip_budget:,})[/yellow]"
                    )
                    continue

                console.print(
                    f"\n[cyan]Trade: sell {obj.sell_player.first_name} {obj.sell_player.last_name}"
                    f" → buy {obj.buy_player.first_name} {obj.buy_player.last_name}"
                    f" (EP +{obj.ep_gain:.1f})[/cyan]"
                )

                sell_result = self._execute_instant_sell(
                    league,
                    obj.sell_player,
                    f"Trade pair: making room for {obj.buy_player.last_name} (EP +{obj.ep_gain:.1f})",
                )
                results.append(sell_result)
                if not sell_result.success:
                    console.print("[red]Sell failed, skipping this trade pair[/red]")
                    continue

                buy_result = self._execute_buy(
                    league,
                    SimpleNamespace(
                        player=obj.buy_player,
                        buy_price=obj.recommended_bid,
                        reason=f"Trade pair: EP +{obj.ep_gain:.1f}",
                    ),
                )
                results.append(buy_result)
                if buy_result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    ctx.flip_budget -= net_cost
                    # Trade pair: slot freed by sell, consumed by buy = net zero
                else:
                    console.print(
                        f"[bold red]⚠ WARNING: Sold {obj.sell_player.last_name} but failed to buy "
                        f"{obj.buy_player.last_name} — squad is now {current_squad_size - 1}/15[/bold red]"
                    )
                    available_slots += 1  # Sell freed a slot but buy failed

        # Execute profit flips with remaining slots
        if profit_flip_candidates and available_slots > 0:
            console.print(
                f"\n[bold cyan]💰 Profit Flips ({len(profit_flip_candidates)} candidates)[/bold cyan]"
            )
            for opp in profit_flip_candidates:
                if ctx.executed_trade_count >= effective_limit:
                    break
                if self.daily_spend >= self.max_daily_spend:
                    break
                if available_slots <= 0:
                    break
                if ctx.my_bid_amounts.get(opp.player.id, 0) > 0:
                    continue
                if opp.buy_price > ctx.flip_budget:
                    continue

                result = self._execute_buy(
                    league,
                    SimpleNamespace(
                        player=opp.player,
                        buy_price=opp.buy_price,
                        reason=f"Flip: +{opp.expected_appreciation:.0f}% in {opp.hold_days}d",
                    ),
                )
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += opp.buy_price
                    ctx.flip_budget -= opp.buy_price
                    available_slots -= 1

        console.print(
            f"\n[green]✓ Executed {ctx.executed_trade_count} trade(s) this session[/green]"
        )
        return results

    @staticmethod
    def _sell_threshold_for_trend(trend_7d_pct: float | None) -> float:
        """Profit% threshold required before selling, based on price momentum.

        Rising players are held longer; falling players are sold earlier.
        """
        if trend_7d_pct is None:
            return 10.0
        if trend_7d_pct >= 5.0:
            return 15.0  # Rising fast — let it ride
        elif trend_7d_pct >= 2.0:
            return 12.0  # Rising — hold a bit longer
        elif trend_7d_pct >= -2.0:
            return 10.0  # Stable — default
        elif trend_7d_pct >= -5.0:
            return 7.0  # Slight decline — take profit sooner
        else:
            return 5.0  # Falling fast — take any profit

    def run_profit_sell_phase(self, league, ctx: EPSessionContext) -> list[AutoTradeResult]:
        """Trend-aware profit sell monitoring.

        Uses formation-aware best-11 to protect true starters, and trend data
        to dynamically adjust sell thresholds. Only sells best-11 members when
        a replacement is lined up in the EP pipeline.
        """
        from .formation import select_best_eleven
        from .trader import Trader

        results = []
        console.print("\n[bold cyan]📈 Profit Sell Monitoring (trend-aware)[/bold cyan]")

        squad = ctx.squad
        if not squad:
            console.print("[dim]No squad loaded[/dim]")
            return results

        squad_scores = ctx.ep_result.get("squad_scores", [])
        if not squad_scores:
            console.print("[yellow]Could not score squad — skipping sell monitoring[/yellow]")
            return results

        # Formation-aware best-11: respects position minimums (1 GK, 3 DEF, 2 MID, 1 FW)
        # This matches what actually plays on matchday. A simple top-N-by-raw-EP sort
        # can wrongly "protect" a 2nd GK and leave a starting midfielder exposed.
        score_map = {s.player_id: s.expected_points for s in squad_scores}
        best_11 = select_best_eleven(squad, score_map)
        best_11_ids = {p.id for p in best_11}

        # Position-minimum protection: never sell if it would break formation.
        # A squad with 0 forwards loses -100 pts every matchday from empty slots.
        from .config import POSITION_MINIMUMS

        position_counts: dict[str, int] = {}
        for p in squad:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1

        # Check if replacements are lined up in the EP pipeline
        buy_recs = ctx.ep_result.get("buy_recs", [])
        trade_pairs = ctx.ep_result.get("trade_pairs", [])
        has_replacement = len(buy_recs) > 0 or len(trade_pairs) > 0

        # Build a Trader instance for trend lookups (uses cached data)
        trader = Trader(
            self.api,
            self.settings,
            bid_learner=self.learner,
            activity_feed_learner=self.activity_feed_learner,
        )

        sell_candidates = []
        for player in squad:
            if player.id in best_11_ids:
                continue

            if not player.buy_price or player.buy_price <= 0:
                continue

            # Hard block: never sell if it would drop a position below its
            # formation minimum. A squad with 0 FW loses -100 pts every matchday.
            pos_min = POSITION_MINIMUMS.get(player.position, 0)
            if position_counts.get(player.position, 0) <= pos_min:
                console.print(
                    f"[dim]Protected {player.last_name} ({player.position}) — "
                    f"at position minimum ({pos_min})[/dim]"
                )
                continue

            profit = player.market_value - player.buy_price
            profit_pct = (profit / player.buy_price) * 100

            # Get trend to determine dynamic threshold
            try:
                trend = trader.trend_service.get_trend(player.id, player.market_value, league.id)
                trend_7d = trend.trend_7d_pct
            except Exception:
                trend_7d = None

            sell_threshold = self._sell_threshold_for_trend(trend_7d)

            # Profit target hit (trend-adjusted)
            if profit_pct >= sell_threshold:
                trend_info = f", trend {trend_7d:+.1f}%/wk" if trend_7d is not None else ""
                sell_candidates.append(
                    (
                        player,
                        profit_pct,
                        f"Profit target ({sell_threshold:.0f}%) hit: +{profit_pct:.1f}% (€{profit:,}{trend_info})",
                    )
                )
            # Stop-loss: only if there's a better player to buy
            elif profit_pct <= -5.0 and has_replacement:
                sell_candidates.append(
                    (
                        player,
                        profit_pct,
                        f"Stop-loss (replacement available): {profit_pct:.1f}% (€{profit:,})",
                    )
                )

        if not sell_candidates:
            console.print("[dim]No players meet sell criteria[/dim]")
            return results

        sell_candidates.sort(key=lambda x: x[1], reverse=True)
        console.print(f"[green]Found {len(sell_candidates)} player(s) to sell[/green]")

        for player, profit_pct, reason in sell_candidates:
            console.print(
                f"\n[cyan]Selling {player.first_name} {player.last_name} to Kickbase "
                f"(MV €{player.market_value:,}, bought €{player.buy_price:,}, "
                f"{profit_pct:+.1f}%)[/cyan]"
            )

            if self.dry_run:
                console.print("[yellow]DRY RUN: Sell not executed[/yellow]")
                results.append(
                    AutoTradeResult(
                        success=True,
                        player_name=f"{player.first_name} {player.last_name}",
                        action="SELL",
                        price=player.market_value,
                        reason=reason,
                        timestamp=time.time(),
                    )
                )
                continue

            try:
                self.api.client.sell_to_kickbase(league.id, player.id)
                console.print(f"[green]✓ Sold {player.first_name} {player.last_name}[/green]")

                if self.learner:
                    try:
                        self.learner.record_flip_outcome(
                            player_id=player.id,
                            buy_price=player.buy_price,
                            sell_price=player.market_value,
                            profit_pct=profit_pct,
                        )
                    except Exception:
                        pass

                results.append(
                    AutoTradeResult(
                        success=True,
                        player_name=f"{player.first_name} {player.last_name}",
                        action="SELL",
                        price=player.market_value,
                        reason=reason,
                        timestamp=time.time(),
                    )
                )
            except Exception as e:
                console.print(
                    f"[red]✗ Failed to sell {player.first_name} {player.last_name}: {e}[/red]"
                )
                results.append(
                    AutoTradeResult(
                        success=False,
                        player_name=f"{player.first_name} {player.last_name}",
                        action="SELL",
                        price=player.market_value,
                        reason=reason,
                        timestamp=time.time(),
                        error=str(e),
                    )
                )

        return results

    def run_full_session(self, league) -> AutoTradeSession:
        """Run a complete automated trading session.

        New unified flow:
        1. Sync activity feed (competitive intelligence)
        2. Build session context (single EP pipeline call + trends + matchday timing)
        3. If locked (0-1 days to match) → set lineup only
        4. Trend-aware profit selling
        5. Squad optimization (budget/size safety)
        6. Unified trade phase (trade pairs compete with plain buys, ranked by EP)
        7. Set optimal lineup
        """
        start_time = time.time()

        console.print(f"\n{'=' * 70}")
        console.print(
            f"[bold]Automated Trading Session - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]"
        )
        if self.dry_run:
            console.print("[yellow]DRY RUN MODE - No trades will be executed[/yellow]")
        console.print(f"{'=' * 70}")

        # Step 0: Sync activity feed for competitive intelligence
        try:
            console.print("\n[dim]Syncing league activity feed...[/dim]")
            activities = self.api.client.get_activities_feed(league.id, start=0)
            stats = self.activity_feed_learner.process_activity_feed(
                activities, api_client=self.api.client
            )
            if stats["transfers_new"] > 0 or stats["market_values_new"] > 0:
                console.print(
                    f"[dim]✓ Synced: {stats['transfers_new']} new transfers, "
                    f"{stats['market_values_new']} new market values[/dim]"
                )
        except Exception as e:
            console.print(f"[yellow]Warning: Could not sync activity feed: {e}[/yellow]")

        sell_results: list[AutoTradeResult] = []
        trade_results: list[AutoTradeResult] = []
        errors: list[str] = []

        # Step 1: Check resolved auctions for learning
        self.check_resolved_auctions(league)

        # Step 2: Build session context (single EP pipeline + trends + matchday phase)
        try:
            ctx = self._build_session_context(league)
        except Exception as e:
            error_msg = f"EP pipeline failed: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)
            # Fall back to just setting lineup
            self._set_optimal_lineup(league, errors)
            return AutoTradeSession(
                start_time=start_time,
                end_time=time.time(),
                profit_trades=[],
                lineup_trades=[],
                errors=errors,
                total_spent=0,
                total_earned=0,
                net_change=0,
            )

        # Step 3: If locked (match imminent), just set lineup and exit
        if ctx.matchday_phase.phase == "locked":
            console.print(
                f"[yellow]Match imminent ({ctx.matchday_phase.days_until_match}d) "
                f"— setting lineup only, no trading[/yellow]"
            )
            self._set_optimal_lineup(league, errors)
            return AutoTradeSession(
                start_time=start_time,
                end_time=time.time(),
                profit_trades=[],
                lineup_trades=[],
                errors=errors,
                total_spent=0,
                total_earned=0,
                net_change=0,
            )

        # Step 4: Trend-aware profit selling
        try:
            sell_results = self.run_profit_sell_phase(league, ctx)
        except Exception as e:
            error_msg = f"Sell monitoring error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Step 5: Squad Optimization (budget/size safety)
        console.print("\n[bold cyan]🎯 Squad Optimization[/bold cyan]")
        try:
            squad_optimization = self.optimize_and_execute_squad(league)
            if squad_optimization and squad_optimization.players_to_sell:
                for player in squad_optimization.players_to_sell:
                    sell_results.append(
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
            error_msg = f"Squad optimization error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Step 6: Bid compliance + quality check
        self._reset_daily_limits_if_needed()
        if self.daily_spend < self.max_daily_spend:
            try:
                from .bid_evaluator import BidEvaluator
                from .league_compliance import LeagueComplianceChecker
                from .trader import Trader

                trader = Trader(
                    self.api,
                    self.settings,
                    bid_learner=self.learner,
                    activity_feed_learner=self.activity_feed_learner,
                )

                market = self.api.get_market(league)
                kickbase_market = [p for p in market if p.is_kickbase_seller()]
                player_trends = {
                    p.id: trader.trend_service.get_trend(p.id, p.market_value, league.id).to_dict()
                    for p in kickbase_market[:50]
                }

                compliance_checker = LeagueComplianceChecker(self.api, self.settings)
                adjusted, canceled = compliance_checker.run_bid_compliance_check(
                    league, player_trends=player_trends, auto_resolve=True, dry_run=self.dry_run
                )
                if adjusted > 0 or canceled > 0:
                    console.print(
                        f"[cyan]Bid compliance: {adjusted} adjusted, {canceled} canceled[/cyan]"
                    )

                bid_evaluator = BidEvaluator(self.api, self.settings)
                bid_evaluations = bid_evaluator.evaluate_active_bids(
                    league, player_trends=player_trends, for_profit=True
                )
                if bid_evaluations:
                    bid_evaluator.display_bid_evaluations(bid_evaluations)
                    canceled_count = bid_evaluator.cancel_bad_bids(
                        league, bid_evaluations, dry_run=self.dry_run
                    )
                    if canceled_count > 0:
                        console.print(
                            f"[yellow]Canceled {canceled_count} bid(s) that no longer make sense[/yellow]"
                        )
            except Exception as e:
                console.print(f"[yellow]Bid compliance check failed: {e}[/yellow]")

        # Step 7: Unified trade phase (EP buys + trade pairs + profit flips)
        try:
            trade_results = self.run_unified_trade_phase(league, ctx)
        except Exception as e:
            error_msg = f"Trading error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Step 8: Set optimal lineup
        self._set_optimal_lineup(league, errors)

        # Calculate totals
        all_results = sell_results + trade_results
        total_spent = sum(r.price for r in all_results if r.action == "BUY" and r.success)
        total_earned = sum(r.price for r in all_results if r.action == "SELL" and r.success)
        net_change = total_earned - total_spent

        end_time = time.time()

        # Print summary
        console.print(f"\n{'=' * 70}")
        console.print("[bold]Session Summary[/bold]")
        console.print(f"{'=' * 70}")
        console.print(f"Duration: {end_time - start_time:.1f}s")
        console.print(f"Phase: {ctx.matchday_phase.phase} ({ctx.matchday_phase.reason})")
        console.print(
            f"Sells: {len([r for r in sell_results if r.success and r.action == 'SELL'])}"
        )
        console.print(
            f"Trades: {len([r for r in trade_results if r.success])}/{len(trade_results)}"
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
            profit_trades=trade_results,
            lineup_trades=sell_results,
            errors=errors,
            total_spent=total_spent,
            total_earned=total_earned,
            net_change=net_change,
        )

    def _set_optimal_lineup(self, league, errors: list[str]):
        """Calculate and set the optimal starting 11 via API"""
        from .expected_points import calculate_expected_points
        from .formation import get_formation_string, order_for_lineup, select_best_eleven
        from .value_history import ValueHistoryCache

        console.print("\n[bold cyan]📋 Setting Optimal Lineup[/bold cyan]")

        try:
            squad = self.api.get_squad(league)
            if not squad or len(squad) < 11:
                console.print("[yellow]Not enough players to set lineup[/yellow]")
                return

            history_cache = ValueHistoryCache()

            # Calculate expected points for each player
            ep_scores = {}
            for player in squad:
                try:
                    perf_data = history_cache.get_cached_performance(
                        player_id=player.id, league_id=league.id, max_age_hours=24
                    )
                    if not perf_data:
                        perf_data = self.api.client.get_player_performance(league.id, player.id)
                        if perf_data:
                            history_cache.cache_performance(
                                player_id=player.id, league_id=league.id, data=perf_data
                            )

                    ep = calculate_expected_points(player=player, performance_data=perf_data)
                    ep_scores[player.id] = ep.expected_points
                except Exception:
                    ep_scores[player.id] = 0

            # Select best 11, order by position for API (GK→DEF→MID→FWD)
            best_eleven = select_best_eleven(squad, ep_scores)
            ordered = order_for_lineup(best_eleven)
            formation = get_formation_string(ordered)
            player_ids = [p.id for p in ordered]

            names = [
                f"{p.first_name[0]}. {p.last_name}" if p.first_name else p.last_name
                for p in ordered
            ]
            console.print(f"[dim]Formation: {formation} | {', '.join(names)}[/dim]")

            if self.dry_run:
                console.print("[yellow]DRY RUN - Lineup not applied[/yellow]")
                return

            self.api.set_lineup(league, formation, player_ids)
            console.print("[green]✓ Lineup set successfully[/green]")

        except Exception as e:
            error_msg = f"Set lineup error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

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
