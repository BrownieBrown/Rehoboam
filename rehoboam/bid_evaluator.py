"""Re-evaluate active bids and recommend actions"""

from dataclasses import dataclass

from rich.console import Console

from rehoboam.config import UNAVAILABLE_STATUSES

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
        self,
        league,
        player_trends: dict | None = None,
        bid_tiers: dict[str, str] | None = None,
        bot_placed_ids: set[str] | None = None,
    ) -> list[BidEvaluation]:
        """Re-read every live offer and decide which the bot may withdraw.

        A bid is a commitment. The safety gate enforced the ceiling once, at
        placement, against that moment's market value; re-judging it here as
        the value drifts is what withdrew Badé, Kleindienst, Harder and
        Castello Jr. between 2026-08-30 and 2026-09-01 — every one an offer
        Marco had approved, two of them auctions the bot would have won.
        Price therefore never cancels. Nor do flip economics: a falling trend
        or a thin appreciation estimate is a reason not to PLACE a bid, not a
        reason to pull one already competing.

        The one thing that cancels is the player: an offer on someone who can
        no longer be fielded (`UNAVAILABLE_STATUSES`) holds a slot and a
        budget for nothing.

        `get_my_bids` is `get_market` filtered by "do we hold an offer" and
        cannot say WHO placed it, so provenance comes from `pending_bids`: a
        recorded tier, or membership in ``bot_placed_ids``. An offer outside
        both was placed by hand and is never cancelled, injured or not —
        Marco can see the injury and bid anyway (REH-115).

        Args:
            league: League object
            player_trends: player_id -> trend dict. Only fills `is_falling`
                on the evaluation, for display; it decides nothing.
            bid_tiers: player_id -> the tier the bid was priced against.
            bot_placed_ids: player ids the bot has an open bid on. None falls
                back to `bid_tiers`, which errs toward leaving a human's bid
                alone.
        """
        bid_tiers = bid_tiers or {}
        bot_bids = set(bid_tiers)
        if bot_placed_ids is not None:
            bot_bids |= {str(pid) for pid in bot_placed_ids}

        evaluations: list[BidEvaluation] = []
        my_bids = self.api.get_my_bids(league)
        if not my_bids:
            return evaluations

        console.print(f"\n[cyan]📊 Evaluating {len(my_bids)} active bids...[/cyan]")

        for bid_player in my_bids:
            player_name = f"{bid_player.first_name} {bid_player.last_name}"
            our_bid = int(bid_player.user_offer_price or 0)
            market_value = int(bid_player.market_value or 0)
            trend = (player_trends or {}).get(bid_player.id, {})
            is_falling = trend.get("trend") == "falling"
            is_unavailable = int(bid_player.status or 0) in UNAVAILABLE_STATUSES
            bid_vs_mv_pct = (
                ((our_bid - market_value) / market_value) * 100 if market_value > 0 else 0.0
            )
            tier = bid_tiers.get(bid_player.id)
            ours_to_cancel = bid_player.id in bot_bids

            recommendation = "KEEP"
            if not ours_to_cancel:
                reason = "Placed manually — not the bot's bid to cancel"
            elif is_unavailable:
                recommendation = "CANCEL"
                reason = f"Player unavailable (status {bid_player.status}) — cannot be fielded"
            elif tier is not None:
                reason = (
                    f"Priced as {tier} at placement — held to resolution "
                    f"({bid_vs_mv_pct:+.1f}% vs market value)"
                )
            else:
                reason = f"Bot bid — held to resolution ({bid_vs_mv_pct:+.1f}% vs market value)"

            evaluations.append(
                BidEvaluation(
                    player_id=bid_player.id,
                    player_name=player_name,
                    our_bid=our_bid,
                    market_value=market_value,
                    recommendation=recommendation,
                    reason=reason,
                    is_injured=is_unavailable,
                    is_falling=is_falling,
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
