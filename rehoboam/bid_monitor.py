"""Bid monitoring and safe replacement execution"""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from .api import KickbaseAPI
from .bid_learner import AuctionOutcome, BidLearner
from .kickbase_client import League

console = Console()


@dataclass
class BidStatus:
    """Status of a pending bid"""

    player_id: str
    player_name: str
    bid_amount: int
    placed_at: float  # timestamp
    status: str  # "pending", "won", "lost", "timeout"
    confirmed_at: float | None = None
    # Additional data for learning
    asking_price: int | None = None
    value_score: float | None = None
    market_value: int | None = None


@dataclass
class ReplacementPlan:
    """Plan for replacing a player after winning bid"""

    target_player_id: str
    target_player_name: str
    players_to_sell: list[dict[str, Any]]  # [{"id": "...", "name": "...", "value": 123}]
    net_profit: int
    expected_budget_after: int


class BidMonitor:
    """Monitors bid status and executes safe replacements"""

    def __init__(
        self,
        api: KickbaseAPI,
        poll_interval: int = 30,
        max_wait_minutes: int = 60,
        state_file: Path | None = None,
        bid_learner: BidLearner | None = None,
    ):
        """
        Initialize bid monitor

        Args:
            api: KickbaseAPI instance
            poll_interval: Seconds between status checks (default: 30)
            max_wait_minutes: Maximum time to wait for auction (default: 60)
            state_file: Path to state file for persistence (default: logs/bid_monitor_state.json)
            bid_learner: Optional BidLearner instance for recording auction outcomes
        """
        self.api = api
        self.poll_interval = poll_interval
        self.max_wait_minutes = max_wait_minutes
        self.pending_bids: dict[str, BidStatus] = {}
        self.replacement_plans: dict[str, ReplacementPlan] = {}

        # Bid learning
        self.bid_learner = bid_learner if bid_learner is not None else BidLearner()

        # State persistence
        if state_file is None:
            state_file = Path("logs") / "bid_monitor_state.json"
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing state
        self._load_state()

    def register_bid(
        self,
        player_id: str,
        player_name: str,
        bid_amount: int,
        replacement_plan: ReplacementPlan | None = None,
        asking_price: int | None = None,
        value_score: float | None = None,
        market_value: int | None = None,
    ):
        """
        Register a bid for monitoring

        Args:
            player_id: ID of player bid on
            player_name: Name of player
            bid_amount: Amount bid
            replacement_plan: Optional replacement plan to execute if bid wins
            asking_price: Player's asking price (for learning)
            value_score: Player's value score (for learning)
            market_value: Player's market value (for learning)
        """
        bid_status = BidStatus(
            player_id=player_id,
            player_name=player_name,
            bid_amount=bid_amount,
            placed_at=time.time(),
            status="pending",
            asking_price=asking_price,
            value_score=value_score,
            market_value=market_value,
        )

        self.pending_bids[player_id] = bid_status

        if replacement_plan:
            self.replacement_plans[player_id] = replacement_plan

        console.print(f"[cyan]Registered bid monitor for {player_name} (€{bid_amount:,})[/cyan]")

        if replacement_plan:
            sell_names = [p["name"] for p in replacement_plan.players_to_sell]
            console.print(
                f"[dim]  Will sell {', '.join(sell_names)} if bid wins "
                f"(net: €{replacement_plan.net_profit:,})[/dim]"
            )

        # Persist state
        self._save_state()

    def check_bid_status(self, league: League, player_id: str) -> str:
        """
        Check if a bid has won/lost by checking market and squad

        Args:
            league: League to check
            player_id: Player ID to check

        Returns:
            "won", "lost", "pending", "timeout", or "unknown"
        """
        if player_id not in self.pending_bids:
            return "unknown"

        bid_status = self.pending_bids[player_id]

        # Check if bid has timed out
        elapsed_minutes = (time.time() - bid_status.placed_at) / 60
        if elapsed_minutes > self.max_wait_minutes:
            bid_status.status = "timeout"
            bid_status.confirmed_at = time.time()
            self._save_state()  # Persist status change
            console.print(
                f"[yellow]Bid on {bid_status.player_name} timed out after {self.max_wait_minutes} minutes[/yellow]"
            )
            return "timeout"

        try:
            # STEP 1: Check market endpoint to see if player still listed
            market_players = self.api.get_market(league)
            player_on_market = next((p for p in market_players if p.id == player_id), None)

            # Get user ID for checking offers
            user_id = self.api.user.id if self.api.user else None

            if player_on_market:
                # Player still on market - check if we have active offer
                if user_id and player_on_market.has_user_offer(user_id):
                    # We have an active offer - auction still pending
                    console.print(
                        f"[dim]Auction still active for {bid_status.player_name} (you have bid: €{player_on_market.user_offer_price:,})[/dim]"
                    )
                    return "pending"
                else:
                    # Player on market but we don't have an offer anymore
                    # Either we were outbid or auction is still going
                    # Check if we have ANY offers on record
                    if player_on_market.offer_count > 0:
                        console.print(
                            f"[yellow]You may have been outbid on {bid_status.player_name} ({player_on_market.offer_count} offer(s) active)[/yellow]"
                        )
                    return "pending"
            else:
                # STEP 2: Player not on market anymore - auction ended
                # Check if we acquired the player
                squad = self.api.get_squad(league)
                player_ids = [p.id for p in squad]

                if player_id in player_ids:
                    # Player in our squad - we won!
                    bid_status.status = "won"
                    bid_status.confirmed_at = time.time()
                    self._save_state()

                    # Record outcome for learning
                    self._record_auction_outcome(league, player_id, won=True)

                    console.print(f"[green]✓ Bid WON for {bid_status.player_name}![/green]")
                    return "won"
                else:
                    # Player not on market AND not in our squad - we lost
                    bid_status.status = "lost"
                    bid_status.confirmed_at = time.time()
                    self._save_state()

                    # Record outcome for learning
                    self._record_auction_outcome(league, player_id, won=False)

                    console.print(f"[red]✗ Bid LOST for {bid_status.player_name}[/red]")
                    return "lost"

        except Exception as e:
            console.print(f"[yellow]Warning: Could not check bid status: {e}[/yellow]")
            return "pending"

    def execute_replacement_if_won(
        self, league: League, player_id: str, dry_run: bool = False
    ) -> bool:
        """
        Check bid status and execute replacement plan if won

        Args:
            league: League
            player_id: Player ID that was bid on
            dry_run: If True, don't actually sell

        Returns:
            True if replacement executed, False otherwise
        """
        status = self.check_bid_status(league, player_id)

        if status != "won":
            return False

        # Bid won - execute replacement plan if exists
        if player_id not in self.replacement_plans:
            console.print(f"[dim]No replacement plan for {player_id}[/dim]")
            return False

        plan = self.replacement_plans[player_id]

        console.print(
            f"\n[bold cyan]Executing Replacement Plan for {plan.target_player_name}[/bold cyan]"
        )

        for player_to_sell in plan.players_to_sell:
            player_sell_id = player_to_sell["id"]
            player_sell_name = player_to_sell["name"]
            player_sell_value = player_to_sell["value"]

            if dry_run:
                console.print(
                    f"[blue][DRY RUN] Would sell {player_sell_name} to KICKBASE for €{player_sell_value:,}[/blue]"
                )
            else:
                try:
                    self.api.client.sell_to_kickbase(league.id, player_sell_id)
                    console.print(
                        f"[green]✓ Sold {player_sell_name} to KICKBASE for €{player_sell_value:,}[/green]"
                    )
                except Exception as e:
                    console.print(f"[red]✗ Failed to sell {player_sell_name}: {e}[/red]")
                    return False

        console.print("\n[bold green]Replacement Complete![/bold green]")
        console.print(f"[green]Net profit: €{plan.net_profit:,}[/green]")
        console.print(f"[green]Expected budget: €{plan.expected_budget_after:,}[/green]")

        # Remove from pending
        del self.replacement_plans[player_id]
        self._save_state()  # Persist state change

        return True

    def monitor_all_bids(self, league: League, dry_run: bool = False):
        """
        Monitor all pending bids and execute replacements when won

        Args:
            league: League to monitor
            dry_run: If True, don't actually sell players
        """
        pending_ids = [
            pid for pid, status in self.pending_bids.items() if status.status == "pending"
        ]

        if not pending_ids:
            console.print("[dim]No pending bids to monitor[/dim]")
            return

        console.print(f"\n[cyan]Monitoring {len(pending_ids)} pending bid(s)...[/cyan]")

        while pending_ids:
            for player_id in list(pending_ids):
                bid_status = self.pending_bids[player_id]
                console.print(
                    f"[dim]Checking {bid_status.player_name} "
                    f"({int((time.time() - bid_status.placed_at) / 60)}m elapsed)...[/dim]"
                )

                # Check status and execute replacement if won
                executed = self.execute_replacement_if_won(league, player_id, dry_run=dry_run)

                status = self.check_bid_status(league, player_id)

                if status in ["won", "lost", "timeout"]:
                    # Remove from pending
                    pending_ids.remove(player_id)

                    if status == "won" and not executed:
                        console.print(
                            f"[yellow]Note: {bid_status.player_name} acquired but no replacement executed[/yellow]"
                        )

            if pending_ids:
                console.print(f"[dim]Waiting {self.poll_interval}s before next check...[/dim]")
                time.sleep(self.poll_interval)

        console.print("[green]All bids resolved![/green]")

    def get_pending_summary(self) -> str:
        """Get summary of pending bids"""
        pending = [b for b in self.pending_bids.values() if b.status == "pending"]

        if not pending:
            return "No pending bids"

        lines = []
        for bid in pending:
            elapsed_min = int((time.time() - bid.placed_at) / 60)
            lines.append(f"  • {bid.player_name}: €{bid.bid_amount:,} ({elapsed_min}m ago)")

        return "\n".join([f"Pending bids ({len(pending)}):"] + lines)

    def _record_auction_outcome(self, league: League, player_id: str, won: bool):
        """Record auction outcome for learning"""
        if player_id not in self.pending_bids:
            return

        bid_status = self.pending_bids[player_id]

        # Calculate overbid percentage
        asking_price = bid_status.asking_price or bid_status.bid_amount
        if asking_price > 0:
            our_overbid_pct = ((bid_status.bid_amount - asking_price) / asking_price) * 100
        else:
            our_overbid_pct = 0.0

        # Try to get winning bid info for losses
        winning_bid = None
        winning_overbid_pct = None
        winner_user_id = None

        if not won:
            try:
                # Get player details to find winner and their bid
                player_details = self.api.client.get_player_details(league.id, player_id)
                winner_user_id = player_details.get("u", None)

                # The winner's bid might be in the player's price history
                # Current market value often reflects the winning bid
                winning_bid = player_details.get("p", None)  # Current price

                if winning_bid and asking_price > 0:
                    winning_overbid_pct = ((winning_bid - asking_price) / asking_price) * 100

            except Exception as e:
                console.print(f"[dim]Could not fetch winner details: {e}[/dim]")

        # Create outcome record
        outcome = AuctionOutcome(
            player_id=player_id,
            player_name=bid_status.player_name,
            our_bid=bid_status.bid_amount,
            asking_price=asking_price,
            our_overbid_pct=our_overbid_pct,
            won=won,
            winning_bid=winning_bid if not won else bid_status.bid_amount,
            winning_overbid_pct=winning_overbid_pct if not won else our_overbid_pct,
            winner_user_id=winner_user_id,
            timestamp=bid_status.confirmed_at or time.time(),
            player_value_score=bid_status.value_score,
            market_value=bid_status.market_value,
        )

        # Record to learning database
        try:
            self.bid_learner.record_outcome(outcome)
            console.print(f"[dim]✓ Recorded auction outcome for learning (won={won})[/dim]")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not record outcome: {e}[/yellow]")

    def _save_state(self):
        """Save bid monitor state to file"""
        state = {
            "pending_bids": {
                player_id: asdict(bid_status) for player_id, bid_status in self.pending_bids.items()
            },
            "replacement_plans": {
                player_id: asdict(plan) for player_id, plan in self.replacement_plans.items()
            },
        }

        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            console.print(f"[dim]Warning: Could not save bid monitor state: {e}[/dim]")

    def _load_state(self):
        """Load bid monitor state from file"""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file) as f:
                state = json.load(f)

            # Restore pending bids
            for player_id, bid_data in state.get("pending_bids", {}).items():
                self.pending_bids[player_id] = BidStatus(**bid_data)

            # Restore replacement plans
            for player_id, plan_data in state.get("replacement_plans", {}).items():
                self.replacement_plans[player_id] = ReplacementPlan(**plan_data)

            # Clean up old/completed bids
            active_count = sum(1 for b in self.pending_bids.values() if b.status == "pending")
            if active_count > 0:
                console.print(
                    f"[dim]Loaded {active_count} active bid(s) from previous session[/dim]"
                )

        except Exception as e:
            console.print(f"[yellow]Warning: Could not load bid monitor state: {e}[/yellow]")
