"""Re-evaluate active bids and recommend actions"""

from dataclasses import dataclass

from rich.console import Console

console = Console()


@dataclass
class BidEvaluation:
    """Result of evaluating an active bid"""

    player_id: str
    player_name: str
    our_bid: int
    market_value: int
    recommendation: str  # KEEP, CANCEL, INCREASE
    reason: str
    suggested_bid: int | None = None  # If INCREASE
    is_injured: bool = False
    is_falling: bool = False
    profit_potential: float = 0.0


class BidEvaluator:
    """Evaluates active bids and recommends actions"""

    def __init__(self, api, settings):
        """
        Args:
            api: KickbaseAPI instance
            settings: Bot settings
        """
        self.api = api
        self.settings = settings

    def evaluate_active_bids(
        self, league, player_trends: dict = None, for_profit: bool = True
    ) -> list[BidEvaluation]:
        """
        Evaluate all active bids

        Args:
            league: League object
            player_trends: Dict mapping player_id -> trend data
            for_profit: If True, evaluate as profit flips. If False, evaluate for lineup

        Returns:
            List of BidEvaluation objects
        """
        evaluations = []

        # Get active bids
        my_bids = self.api.get_my_bids(league)

        if not my_bids:
            return evaluations

        console.print(f"\n[cyan]📊 Evaluating {len(my_bids)} active bids...[/cyan]")

        for bid_player in my_bids:
            player_name = f"{bid_player.first_name} {bid_player.last_name}"
            our_bid = bid_player.user_offer_price
            market_value = bid_player.market_value

            # Get trend data if available
            trend = {}
            if player_trends:
                trend = player_trends.get(bid_player.id, {})

            trend_direction = trend.get("trend", "unknown")
            trend_pct = trend.get("trend_pct", 0)
            peak_value = trend.get("peak_value", 0)
            current_value = trend.get("current_value", market_value)

            # Check injury status
            is_injured = bid_player.status != 0

            # Check if falling
            is_falling = trend_direction == "falling"

            # Calculate how much over market value we're bidding
            bid_vs_mv_pct = (
                ((our_bid - market_value) / market_value) * 100 if market_value > 0 else 0
            )

            # Decision logic
            recommendation = "KEEP"
            reason = ""
            suggested_bid = None
            profit_potential = 0.0  # Initialize here

            # CANCEL conditions
            if is_injured:
                recommendation = "CANCEL"
                reason = f"Player is injured (status: {bid_player.status})"

            elif is_falling and trend_pct < -10 and for_profit:
                # Falling trend - but check if it's a mean reversion opportunity first
                # Mean reversion: player far below peak (>50%) with good performance
                # peak_value and current_value already extracted above (lines 74-75)

                is_mean_reversion = False
                if peak_value > 0:
                    current_vs_peak_pct = ((current_value - peak_value) / peak_value) * 100
                    if current_vs_peak_pct < -50 and bid_player.average_points >= 40:
                        # Mean reversion opportunity: >50% below peak + good performer
                        is_mean_reversion = True

                if not is_mean_reversion:
                    # Not a mean reversion play - cancel falling bid
                    recommendation = "CANCEL"
                    reason = f"Falling trend ({trend_pct:.1f}%) - not good for flips"

            elif for_profit and bid_vs_mv_pct > 25:
                # For profit flips, don't bid >25% over market value (relaxed from 15%)
                recommendation = "CANCEL"
                reason = f"Bid {bid_vs_mv_pct:.1f}% over market value - too expensive for flip"

            # KEEP conditions
            else:
                if for_profit:
                    # Calculate expected profit potential
                    # Accept rising trends, stable good performers, or mean reversion plays
                    expected_appreciation = 0
                    if trend_direction == "rising" and trend_pct > 5:
                        expected_appreciation = min(trend_pct, 20)
                    elif trend_direction == "stable" and bid_player.average_points >= 40:
                        # Stable good performers - conservative estimate
                        expected_appreciation = 8
                    elif trend_direction == "falling":
                        # Check for mean reversion opportunity
                        # peak_value and current_value already extracted above (lines 74-75)
                        if peak_value > 0:
                            current_vs_peak_pct = ((current_value - peak_value) / peak_value) * 100
                            if current_vs_peak_pct < -50 and bid_player.average_points >= 40:
                                # Mean reversion play
                                expected_appreciation = min(abs(current_vs_peak_pct) * 0.3, 15)

                    profit_potential = expected_appreciation

                    if profit_potential >= 8:  # Relaxed from 10%
                        reason = (
                            f"Good flip potential: {profit_potential:.1f}% expected appreciation"
                        )
                    else:
                        recommendation = "CANCEL"
                        reason = f"Low profit potential: {profit_potential:.1f}% (need >= 8%)"
                else:
                    # For lineup improvements, more lenient
                    if bid_player.average_points > 50 and not is_falling:
                        reason = f"High performer ({bid_player.average_points:.1f} pts/game) - worth keeping"
                    elif bid_vs_mv_pct <= 20:
                        reason = f"Reasonable bid ({bid_vs_mv_pct:+.1f}% vs market value)"
                    else:
                        recommendation = "CANCEL"
                        reason = f"Bid too high: {bid_vs_mv_pct:+.1f}% over market value"

            evaluations.append(
                BidEvaluation(
                    player_id=bid_player.id,
                    player_name=player_name,
                    our_bid=our_bid,
                    market_value=market_value,
                    recommendation=recommendation,
                    reason=reason,
                    suggested_bid=suggested_bid,
                    is_injured=is_injured,
                    is_falling=is_falling,
                    profit_potential=profit_potential if for_profit else 0,
                )
            )

        return evaluations

    def display_bid_evaluations(self, evaluations: list[BidEvaluation]):
        """Display bid evaluation results"""
        if not evaluations:
            console.print("[dim]No active bids to evaluate[/dim]")
            return

        keep_count = sum(1 for e in evaluations if e.recommendation == "KEEP")
        cancel_count = sum(1 for e in evaluations if e.recommendation == "CANCEL")

        console.print("\n[bold]Bid Evaluation Summary:[/bold]")
        console.print(f"  Keep: {keep_count}")
        console.print(f"  Cancel: {cancel_count}")

        if cancel_count > 0:
            console.print(f"\n[yellow]⚠️  Recommend canceling {cancel_count} bid(s):[/yellow]")
            for eval in evaluations:
                if eval.recommendation == "CANCEL":
                    console.print(f"\n  [red]❌ {eval.player_name}[/red]")
                    console.print(f"     Your bid: €{eval.our_bid:,}")
                    console.print(f"     Market value: €{eval.market_value:,}")
                    console.print(f"     Reason: {eval.reason}")

        if keep_count > 0:
            console.print("\n[green]✓ Keep these bids:[/green]")
            for eval in evaluations:
                if eval.recommendation == "KEEP":
                    console.print(f"\n  [green]✓ {eval.player_name}[/green]")
                    console.print(f"     Your bid: €{eval.our_bid:,}")
                    console.print(f"     Market value: €{eval.market_value:,}")
                    console.print(f"     {eval.reason}")

    def cancel_bad_bids(
        self, league, evaluations: list[BidEvaluation], dry_run: bool = False
    ) -> int:
        """
        Cancel bids that are recommended to cancel

        Args:
            league: League object
            evaluations: List of BidEvaluation objects
            dry_run: If True, simulate but don't execute

        Returns:
            Number of bids canceled
        """
        canceled = 0

        for eval in evaluations:
            if eval.recommendation == "CANCEL":
                console.print(f"\n[yellow]Canceling bid on {eval.player_name}...[/yellow]")
                console.print(f"[dim]Reason: {eval.reason}[/dim]")

                if dry_run:
                    console.print("[yellow]DRY RUN: Bid not canceled[/yellow]")
                    canceled += 1
                else:
                    try:
                        # Find the player object
                        market = self.api.get_market(league)
                        player = next((p for p in market if p.id == eval.player_id), None)

                        if player:
                            self.api.cancel_bid(league, player)
                            console.print(f"[green]✓ Bid canceled on {eval.player_name}[/green]")
                            canceled += 1
                        else:
                            console.print("[red]✗ Could not find player in market[/red]")

                    except Exception as e:
                        console.print(f"[red]✗ Failed to cancel bid: {e}[/red]")

        return canceled
