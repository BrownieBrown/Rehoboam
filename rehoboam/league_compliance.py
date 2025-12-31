"""League compliance checker for market value rules"""

from dataclasses import dataclass

from rich.console import Console

console = Console()


@dataclass
class ComplianceIssue:
    """A player that violates league rules"""

    player_id: str
    player_name: str
    purchase_price: int
    current_market_value: int
    violation_pct: float  # How much below market value
    violation_amount: int
    reason: str


@dataclass
class BidComplianceIssue:
    """An active bid that violates league rules"""

    player_id: str
    player_name: str
    current_bid: int
    market_value: int
    asking_price: int
    violation_amount: int
    violation_pct: float
    new_required_bid: int  # Market value + buffer
    is_still_profitable: bool
    predicted_value: int | None
    reason: str


class LeagueComplianceChecker:
    """Checks for and resolves league rule violations"""

    def __init__(self, api, settings):
        """
        Args:
            api: KickbaseAPI instance
            settings: Bot settings
        """
        self.api = api
        self.settings = settings

    def check_market_value_compliance(self, league) -> list[ComplianceIssue]:
        """
        Check if any owned players violate market value rules.

        League Rule: Cannot own players purchased below their current market value.
        Market values update daily after 10:00.

        Returns:
            List of ComplianceIssue objects for players violating the rule
        """
        import json
        from pathlib import Path

        issues = []

        # Get current squad
        my_team = self.api.get_squad(league)

        # Load purchase tracking to know what we paid
        purchases_file = Path("logs") / "tracked_purchases.json"
        if not purchases_file.exists():
            console.print("[yellow]No purchase tracking data - cannot check compliance[/yellow]")
            console.print("[dim]Purchases made by the bot are automatically tracked[/dim]")
            return issues

        with open(purchases_file) as f:
            purchases = json.load(f)

        # Check each player
        for player in my_team:
            if player.id not in purchases:
                # Player not tracked (bought manually or before tracking started)
                continue

            purchase_data = purchases[player.id]
            purchase_price = purchase_data["buy_price"]
            current_market_value = player.market_value

            # Check if purchased below market value
            if purchase_price < current_market_value:
                violation_amount = current_market_value - purchase_price
                violation_pct = (violation_amount / current_market_value) * 100

                issues.append(
                    ComplianceIssue(
                        player_id=player.id,
                        player_name=f"{player.first_name} {player.last_name}",
                        purchase_price=purchase_price,
                        current_market_value=current_market_value,
                        violation_pct=violation_pct,
                        violation_amount=violation_amount,
                        reason=f"Purchased at €{purchase_price:,}, market value now €{current_market_value:,}",
                    )
                )

        return issues

    def display_compliance_issues(self, issues: list[ComplianceIssue]):
        """Display compliance violations"""
        if not issues:
            console.print("[green]✓ No compliance issues - all players legal[/green]")
            return

        console.print(f"\n[red]⚠️  Found {len(issues)} player(s) violating market value rule:[/red]")

        for issue in issues:
            console.print(f"\n  [red]❌ {issue.player_name}[/red]")
            console.print(f"     Purchased at: €{issue.purchase_price:,}")
            console.print(f"     Market value: €{issue.current_market_value:,}")
            console.print(
                f"     Violation: €{issue.violation_amount:,} ({issue.violation_pct:.1f}% below market)"
            )
            console.print("     Action: [yellow]MUST SELL to comply with league rules[/yellow]")

    def resolve_compliance_issues(
        self, league, issues: list[ComplianceIssue], dry_run: bool = False
    ) -> int:
        """
        Automatically sell players that violate compliance rules.

        Args:
            league: League object
            issues: List of ComplianceIssue objects
            dry_run: If True, simulate but don't execute

        Returns:
            Number of players sold for compliance
        """
        if not issues:
            return 0

        console.print(f"\n[yellow]Resolving {len(issues)} compliance violation(s)...[/yellow]")

        sold_count = 0

        for issue in issues:
            console.print(f"\n[yellow]Selling {issue.player_name} for compliance...[/yellow]")
            console.print(f"[dim]Reason: {issue.reason}[/dim]")
            console.print(f"[dim]Selling at market value: €{issue.current_market_value:,}[/dim]")

            if dry_run:
                console.print("[yellow]DRY RUN: Player not sold[/yellow]")
                sold_count += 1
            else:
                try:
                    # Sell at current market value
                    self.api.list_player(
                        league_id=league.id,
                        player_id=issue.player_id,
                        price=issue.current_market_value,
                    )
                    console.print(f"[green]✓ {issue.player_name} listed for sale[/green]")
                    sold_count += 1

                    # Remove from purchase tracking
                    import json
                    from pathlib import Path

                    purchases_file = Path("logs") / "tracked_purchases.json"
                    if purchases_file.exists():
                        with open(purchases_file) as f:
                            purchases = json.load(f)
                        if issue.player_id in purchases:
                            del purchases[issue.player_id]
                            with open(purchases_file, "w") as f:
                                json.dump(purchases, f, indent=2)

                except Exception as e:
                    console.print(f"[red]✗ Failed to sell {issue.player_name}: {e}[/red]")

        return sold_count

    def run_compliance_check(self, league, auto_resolve: bool = True, dry_run: bool = False):
        """
        Run full compliance check and optionally resolve issues.

        Args:
            league: League object
            auto_resolve: If True, automatically sell non-compliant players
            dry_run: If True, simulate but don't execute sales

        Returns:
            Number of issues resolved
        """
        console.print("\n[cyan]🔍 Checking league compliance (market value rule)...[/cyan]")

        issues = self.check_market_value_compliance(league)
        self.display_compliance_issues(issues)

        if issues and auto_resolve:
            return self.resolve_compliance_issues(league, issues, dry_run=dry_run)

        return 0

    def check_bid_compliance(self, league, player_trends: dict = None) -> list[BidComplianceIssue]:
        """
        Check if any active bids violate market value rules.

        League Rule: Cannot bid below market value. Market values update daily after 10:00.

        Args:
            league: League object
            player_trends: Optional dict of player trends for profit calculations

        Returns:
            List of BidComplianceIssue objects for bids violating the rule
        """
        issues = []

        # Get active bids
        my_bids = self.api.get_my_bids(league)

        if not my_bids:
            return issues

        console.print(f"\n[cyan]📊 Checking {len(my_bids)} active bid(s) for compliance...[/cyan]")

        # Check each bid
        for bid_player in my_bids:
            player_name = f"{bid_player.first_name} {bid_player.last_name}"
            our_bid = bid_player.user_offer_price
            market_value = bid_player.market_value
            asking_price = bid_player.price

            # Check if our bid is below market value (illegal!)
            if our_bid < market_value:
                violation_amount = market_value - our_bid
                violation_pct = (violation_amount / market_value) * 100

                # Calculate new required bid (market value + 2% buffer)
                buffer_pct = 2.0
                new_required_bid = int(market_value * (1 + buffer_pct / 100))

                # Check if still profitable
                # Get predicted future value from trends if available
                predicted_value = None
                is_still_profitable = True  # Default to true

                if player_trends and bid_player.id in player_trends:
                    trend_data = player_trends[bid_player.id]
                    # Estimate future value based on trend
                    trend_pct = trend_data.get("trend_pct", 0)
                    if trend_pct > 0:
                        # Rising player - estimate future value
                        predicted_value = int(market_value * (1 + min(trend_pct, 20) / 100))
                    else:
                        # Falling or stable - use market value as ceiling
                        predicted_value = market_value

                    # Check profitability: need at least 10% profit margin
                    min_profit_margin = new_required_bid * 0.10
                    is_still_profitable = (
                        predicted_value and predicted_value > new_required_bid + min_profit_margin
                    )
                else:
                    # No trend data - assume profitable if market value is reasonable
                    # Simple check: if new bid would be <15% over asking price
                    bid_vs_asking = ((new_required_bid - asking_price) / asking_price) * 100
                    is_still_profitable = bid_vs_asking < 15.0

                reason = f"Bid €{our_bid:,} below market value €{market_value:,}"
                if is_still_profitable:
                    reason += f" → Adjust to €{new_required_bid:,}"
                else:
                    reason += " → Cancel (no longer profitable)"

                issues.append(
                    BidComplianceIssue(
                        player_id=bid_player.id,
                        player_name=player_name,
                        current_bid=our_bid,
                        market_value=market_value,
                        asking_price=asking_price,
                        violation_amount=violation_amount,
                        violation_pct=violation_pct,
                        new_required_bid=new_required_bid,
                        is_still_profitable=is_still_profitable,
                        predicted_value=predicted_value,
                        reason=reason,
                    )
                )

        return issues

    def display_bid_compliance_issues(self, issues: list[BidComplianceIssue]):
        """Display bid compliance violations"""
        if not issues:
            console.print("[green]✓ All active bids comply with market value rule[/green]")
            return

        console.print(f"\n[yellow]⚠️  Found {len(issues)} bid(s) below market value:[/yellow]")

        adjust_count = sum(1 for i in issues if i.is_still_profitable)
        cancel_count = sum(1 for i in issues if not i.is_still_profitable)

        console.print(f"  To adjust: {adjust_count}")
        console.print(f"  To cancel: {cancel_count}")

        for issue in issues:
            if issue.is_still_profitable:
                console.print(f"\n  [yellow]⚠️  {issue.player_name}[/yellow]")
                console.print(f"     Current bid: €{issue.current_bid:,}")
                console.print(f"     Market value: €{issue.market_value:,}")
                console.print(
                    f"     Violation: €{issue.violation_amount:,} ({issue.violation_pct:.1f}% below)"
                )
                console.print(
                    f"     Action: [cyan]Adjust bid to €{issue.new_required_bid:,}[/cyan]"
                )
                if issue.predicted_value:
                    profit = issue.predicted_value - issue.new_required_bid
                    console.print(
                        f"     Estimated profit: €{profit:,} (predicted value: €{issue.predicted_value:,})"
                    )
            else:
                console.print(f"\n  [red]❌ {issue.player_name}[/red]")
                console.print(f"     Current bid: €{issue.current_bid:,}")
                console.print(f"     Market value: €{issue.market_value:,}")
                console.print(
                    f"     Would need to bid: €{issue.new_required_bid:,} (not profitable)"
                )
                console.print("     Action: [red]Cancel bid[/red]")

    def resolve_bid_compliance_issues(
        self, league, issues: list[BidComplianceIssue], dry_run: bool = False
    ) -> tuple[int, int]:
        """
        Automatically adjust or cancel bids that violate compliance rules.

        Args:
            league: League object
            issues: List of BidComplianceIssue objects
            dry_run: If True, simulate but don't execute

        Returns:
            Tuple of (adjusted_count, canceled_count)
        """
        if not issues:
            return 0, 0

        console.print(f"\n[yellow]Resolving {len(issues)} bid compliance violation(s)...[/yellow]")

        adjusted = 0
        canceled = 0

        for issue in issues:
            # Get current market to find player object
            market = self.api.get_market(league)
            player = next((p for p in market if p.id == issue.player_id), None)

            if not player:
                console.print(f"[red]✗ Could not find {issue.player_name} in market[/red]")
                continue

            if issue.is_still_profitable:
                # Adjust bid: cancel old, place new
                console.print(f"\n[yellow]Adjusting bid on {issue.player_name}...[/yellow]")
                console.print(
                    f"[dim]Old bid: €{issue.current_bid:,} → New bid: €{issue.new_required_bid:,}[/dim]"
                )

                if dry_run:
                    console.print("[yellow]DRY RUN: Bid not adjusted[/yellow]")
                    adjusted += 1
                else:
                    try:
                        # Cancel old bid
                        if player.user_offer_id:
                            self.api.cancel_bid(league, player)
                            console.print("[dim]✓ Canceled old bid[/dim]")

                        # Place new bid at market value + buffer
                        self.api.buy_player(league, player, issue.new_required_bid)
                        console.print(
                            f"[green]✓ Adjusted bid to €{issue.new_required_bid:,}[/green]"
                        )
                        adjusted += 1

                    except Exception as e:
                        console.print(
                            f"[red]✗ Failed to adjust bid on {issue.player_name}: {e}[/red]"
                        )

            else:
                # Cancel bid - no longer profitable
                console.print(f"\n[red]Canceling bid on {issue.player_name}...[/red]")
                console.print(
                    f"[dim]Reason: Would need €{issue.new_required_bid:,} but not profitable[/dim]"
                )

                if dry_run:
                    console.print("[yellow]DRY RUN: Bid not canceled[/yellow]")
                    canceled += 1
                else:
                    try:
                        if player.user_offer_id:
                            self.api.cancel_bid(league, player)
                            console.print(f"[green]✓ Canceled bid on {issue.player_name}[/green]")
                            canceled += 1
                    except Exception as e:
                        console.print(
                            f"[red]✗ Failed to cancel bid on {issue.player_name}: {e}[/red]"
                        )

        return adjusted, canceled

    def run_bid_compliance_check(
        self,
        league,
        player_trends: dict = None,
        auto_resolve: bool = True,
        dry_run: bool = False,
    ) -> tuple[int, int]:
        """
        Run full bid compliance check and optionally resolve issues.

        Args:
            league: League object
            player_trends: Optional player trend data for profitability checks
            auto_resolve: If True, automatically adjust/cancel non-compliant bids
            dry_run: If True, simulate but don't execute

        Returns:
            Tuple of (adjusted_count, canceled_count)
        """
        issues = self.check_bid_compliance(league, player_trends=player_trends)
        self.display_bid_compliance_issues(issues)

        if issues and auto_resolve:
            return self.resolve_bid_compliance_issues(league, issues, dry_run=dry_run)

        return 0, 0
