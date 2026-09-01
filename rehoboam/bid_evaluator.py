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


def untiered_price_ceiling(market_value: int, policy) -> int:
    """The price ceiling for an open bid with no recorded tier.

    A bid reaches here when `pending_bids` has no tier for it: rows written
    before REH-111 added the column, a bid placed straight through
    `ExecutionService.buy` by a path that does not carry one, or a bid the DB
    lost track of. The choice is a real trade-off and it governs real money:

      * **Tightest (`Tier.MARGINAL`, 8%)** — mirrors `bid_ceiling.FALLBACK_TIER`,
        whose stated rule is that a proposal must not buy itself a larger
        ceiling by losing its tier. Consistent, but it cancels *more* than the
        old flat cap did, so every legacy bid in flight gets withdrawn on the
        next session — the exact failure REH-111 exists to stop.
      * **Status quo (flat 25%)** — what shipped before REH-111. Cancels only
        the must-have band, i.e. only the bids we most wanted to keep.
      * **Widest (`Tier.MUST_HAVE`, 35%)** — never cancels a bid the bidder
        could legally have placed. Safe against false cancels; lets a genuinely
        overpriced untiered flip bid ride until it resolves or expires.

    TODO(marco): pick the policy and replace the body. The placeholder below
    preserves pre-REH-111 behaviour so nothing regresses while undecided.

    Args:
        market_value: The player's current market value, in euros.
        policy: The `BidCeilingPolicy` from `Settings.bid_ceiling_policy()`;
            use `policy.max_bid(market_value, tier)` to price against a tier.

    Returns:
        The highest bid, in euros, that may stand without being cancelled.
    """
    return int(market_value * 1.25)


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

    def _price_ceiling(self, market_value: int, tier: str | None) -> int:
        """The highest this bid may stand at before it counts as too expensive.

        One definition, shared with the bidder and the gate (REH-99), so a bid
        the bidder was allowed to place cannot be cancelled here for its price.
        A flat cap cannot do this job: it sat at 25%, exactly the `strong`
        ceiling, which made `must_have` the only tier the evaluator could
        cancel — the highest-EP acquisitions, and nothing else (REH-111).
        """
        policy = self.settings.bid_ceiling_policy()
        if tier is not None:
            return policy.max_bid(market_value, tier)
        return untiered_price_ceiling(market_value, policy)

    def evaluate_active_bids(
        self,
        league,
        player_trends: dict = None,
        for_profit: bool = True,
        bid_tiers: dict[str, str] | None = None,
        bot_placed_ids: set[str] | None = None,
    ) -> list[BidEvaluation]:
        """
        Evaluate all active bids

        Args:
            league: League object
            player_trends: Dict mapping player_id -> trend data
            for_profit: If True, evaluate as profit flips. If False, evaluate for lineup
            bid_tiers: Dict mapping player_id -> the tier the bid was priced
                against, from `pending_bids`. A player absent from this map has
                no recorded tier — see `untiered_price_ceiling`.
            bot_placed_ids: Player ids the bot has an open bid on, from
                `pending_bids`. Offers outside this set were placed by hand and
                are never cancelled (REH-115). A recorded tier is itself proof
                of provenance, so passing None falls back to `bid_tiers` —
                which errs toward leaving a human's bid alone.

        Returns:
            List of BidEvaluation objects
        """
        bid_tiers = bid_tiers or {}

        # REH-115: `get_my_bids` is `get_market` filtered by "do we hold an
        # offer" and cannot say WHO placed it, so every offer Marco makes by
        # hand arrives here looking like an unexplained bot bid. On 2026-09-01
        # that cancelled his Conrad Harder offer at 34.4% over market value.
        # A recorded tier proves the bot placed it; `pending_bids` membership
        # proves it for rows predating the tier column.
        bot_bids = set(bid_tiers)
        if bot_placed_ids is not None:
            bot_bids |= {str(pid) for pid in bot_placed_ids}
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

            # A bid carries the intent it was placed with (REH-111). Flip
            # economics — a falling market-value trend, the appreciation floor
            # — only decide a bid taken for appreciation. A tiered bid was
            # priced for matchday points, and a must-have that never gains a
            # euro of market value still scores every week we hold it.
            tier = bid_tiers.get(bid_player.id)
            judge_as_flip = for_profit and tier is None
            # Marco's own judgement is an input, not a candidate for review.
            ours_to_cancel = bid_player.id in bot_bids

            # Decision logic
            recommendation = "KEEP"
            reason = ""
            suggested_bid = None
            profit_potential = 0.0  # Initialize here

            # CANCEL conditions
            if not ours_to_cancel:
                reason = "Placed manually — not the bot's bid to cancel"

            elif is_injured:
                recommendation = "CANCEL"
                reason = f"Player is injured (status: {bid_player.status})"

            elif is_falling and trend_pct < -10 and judge_as_flip:
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

            elif for_profit and our_bid > self._price_ceiling(market_value, tier):
                # Above the ceiling this bid was actually priced against.
                recommendation = "CANCEL"
                reason = f"Bid {bid_vs_mv_pct:.1f}% over market value - above its ceiling"

            # KEEP conditions
            else:
                if tier is not None:
                    # Within the ceiling it was priced against, and held for
                    # points rather than appreciation — nothing left to fail.
                    reason = f"Priced as {tier} — within its ceiling"
                elif judge_as_flip:
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
