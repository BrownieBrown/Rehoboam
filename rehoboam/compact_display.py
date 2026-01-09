"""Compact, action-oriented display for analyze command"""

from rich.console import Console
from rich.table import Table

console = Console()


class CompactDisplay:
    """Compact display for trading analysis"""

    def __init__(self, bidding_strategy=None):
        self.bidding = bidding_strategy

    def get_sos_indicator(self, rating: str | None) -> str:
        """Get compact visual indicator for strength of schedule"""
        if not rating:
            return "→"
        if rating == "Very Easy":
            return "⚡⚡⚡"
        elif rating == "Easy":
            return "⚡⚡"
        elif rating == "Very Difficult":
            return "🔥🔥🔥"
        elif rating == "Difficult":
            return "🔥🔥"
        else:
            return "→"

    def display_action_plan(
        self,
        buy_opportunities: list,
        sell_urgent: list,
        flip_opportunities: list,
        squad_summary: dict,
        market_insights: dict,
        is_emergency: bool = False,
        squad_size: int = 0,
    ):
        """
        Display compact action plan

        Args:
            buy_opportunities: Top buy recommendations (max 5)
            sell_urgent: Urgent sell recommendations only
            flip_opportunities: Quick flip opportunities (max 3)
            squad_summary: Dict with squad stats
            market_insights: Dict with market intelligence
            is_emergency: True if squad below minimum
            squad_size: Current squad size
        """
        console.print("\n" + "═" * 80)
        console.print("[bold cyan]🎯  ACTION PLAN[/bold cyan]")
        console.print("═" * 80 + "\n")

        # BUY NOW Section
        if buy_opportunities:
            if is_emergency:
                console.print(
                    f"[bold red]🚨 URGENT: FILL SQUAD ({len(buy_opportunities)} recommendations)[/bold red]"
                )
                console.print(
                    f"[yellow]Squad critically low ({squad_size}/11) - relaxed quality standards to fill roster[/yellow]\n"
                )
            else:
                console.print(
                    f"[bold green]🟢 BUY NOW ({len(buy_opportunities)} opportunities)[/bold green]"
                )
                console.print(
                    "[dim]Rising/stable, healthy, only very difficult schedules filtered out[/dim]\n"
                )
            self._display_buy_table(buy_opportunities[: 8 if is_emergency else 5])
        else:
            if is_emergency:
                console.print(
                    "[bold red]🚨 CRITICAL: NO PLAYERS AVAILABLE TO FILL SQUAD[/bold red]"
                )
                console.print(
                    f"[yellow]Squad at {squad_size}/11 - you need players urgently![/yellow]"
                )
                console.print(
                    "[dim]All available players are either injured or very low quality[/dim]"
                )
                console.print(
                    "[dim]Run 'rehoboam analyze --detailed --all' to see all market players[/dim]\n"
                )
            else:
                console.print("[bold yellow]🟡 NO BUY RECOMMENDATIONS TODAY[/bold yellow]")
                console.print("[dim]No players meet quality standards:[/dim]")
                console.print(
                    "[dim]  • Score 40+, rising/stable trend, healthy, not very difficult schedule[/dim]"
                )
                console.print(
                    "[dim]✓ This is normal - the bot only recommends good opportunities[/dim]"
                )
                console.print(
                    "[dim]✓ New players are added to the market daily - check back later[/dim]\n"
                )

        # SELL NOW Section
        if sell_urgent:
            console.print("\n[bold red]🔴 SELL NOW (Urgent)[/bold red]\n")
            self._display_sell_table(sell_urgent)
        else:
            console.print("\n[bold green]✓ NO URGENT SELLS[/bold green]")
            console.print(
                "[dim]Your squad looks healthy - no players need immediate selling[/dim]\n"
            )

        # QUICK FLIPS Section (optional)
        if flip_opportunities:
            console.print("\n[bold yellow]💰 QUICK FLIPS (Optional - Profit Trading)[/bold yellow]")
            console.print("[dim]Short-term trades for profit (3-7 day holds)[/dim]\n")
            self._display_flip_table(flip_opportunities[:3])

        # Squad Overview
        console.print("\n" + "═" * 80)
        console.print("[bold cyan]📊  SQUAD OVERVIEW[/bold cyan]")
        console.print("═" * 80)
        self._display_squad_summary(squad_summary)

        # Market Intel
        if market_insights:
            console.print("\n" + "═" * 80)
            console.print("[bold cyan]💡  MARKET INTEL[/bold cyan]")
            console.print("═" * 80)
            self._display_market_insights(market_insights)

        console.print("\n" + "═" * 80)
        console.print("[bold]📋 QUALITY STANDARDS FOR BUY RECOMMENDATIONS[/bold]")
        if is_emergency:
            console.print(
                "[yellow]EMERGENCY MODE - Relaxed standards due to low squad size:[/yellow]"
            )
            console.print("[dim]✓ Value score 40+ (acceptable fundamentals)[/dim]")
            console.print("[dim]✓ Healthy status (no injured players)[/dim]")
            console.print("[dim]✓ Any trend or schedule (focus on filling roster)[/dim]")
        else:
            console.print("[dim]✓ Value score 50+ (strong fundamentals)[/dim]")
            console.print("[dim]✓ Rising or stable trend (falling < -10% rejected)[/dim]")
            console.print("[dim]✓ Healthy status (no injured players)[/dim]")
            console.print("[dim]✓ Not very difficult schedule (difficult schedules OK)[/dim]")
            console.print("[dim]✓ Not extremely hard next matchup (moderate difficulty OK)[/dim]")
        console.print("\n[dim]Run 'rehoboam analyze --detailed' for full analysis[/dim]")
        console.print("═" * 80 + "\n")

    def _display_buy_table(self, opportunities: list):
        """Display compact buy recommendations table"""
        table = Table(show_header=True, header_style="bold green", box=None)
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Pos", style="blue", width=3)
        table.add_column("Smart Bid", justify="right", style="yellow")
        table.add_column("Score", justify="right", style="magenta", width=5)
        table.add_column("Next 3", justify="center", style="dim", width=7)
        table.add_column("Days", justify="center", style="dim", width=4)
        table.add_column("Why", style="dim")

        for analysis in opportunities:
            player = analysis.player
            name = f"{player.first_name} {player.last_name}"

            # Calculate smart bid
            if self.bidding:
                bid_rec = self.bidding.calculate_bid(
                    asking_price=analysis.current_price,
                    market_value=analysis.market_value,
                    value_score=analysis.value_score,
                    confidence=analysis.confidence,
                    player_id=player.id,
                )
                bid_str = f"€{bid_rec.recommended_bid:,}\n+{bid_rec.overbid_pct:.0f}%"
            else:
                bid_str = f"€{analysis.current_price:,}"

            # Get SOS from metadata or reason string
            sos_indicator = "→"
            if hasattr(analysis, "metadata") and analysis.metadata:
                sos_rating = analysis.metadata.get("sos_rating")
                if sos_rating:
                    sos_indicator = self.get_sos_indicator(sos_rating)

            # Extract SOS from reason string as fallback
            if sos_indicator == "→":
                if "⚡⚡⚡" in analysis.reason or "Very Easy" in analysis.reason:
                    sos_indicator = "⚡⚡⚡"
                elif "⚡⚡" in analysis.reason or "Easy" in analysis.reason:
                    sos_indicator = "⚡⚡"
                elif "🔥🔥🔥" in analysis.reason or "Very Difficult" in analysis.reason:
                    sos_indicator = "🔥🔥🔥"
                elif "🔥🔥" in analysis.reason or "Difficult" in analysis.reason:
                    sos_indicator = "🔥🔥"

            # Days listed
            days_str = "-"
            if hasattr(player, "listed_at") and player.listed_at:
                try:
                    from datetime import datetime

                    listed_dt = datetime.fromisoformat(player.listed_at.replace("Z", "+00:00"))
                    days = (datetime.now(listed_dt.tzinfo) - listed_dt).days
                    days_str = f"{days}d" if days > 0 else "<1d"
                except Exception:
                    pass

            # Compact reason - extract key points
            reason_parts = []

            # Trend is most important - show it first
            if analysis.trend and analysis.trend_change_pct:
                if "rising" in analysis.trend.lower():
                    if analysis.trend_change_pct > 15:
                        reason_parts.append(f"🚀 +{analysis.trend_change_pct:.0f}%")
                    elif analysis.trend_change_pct > 5:
                        reason_parts.append(f"↗ +{analysis.trend_change_pct:.0f}%")
                    else:
                        reason_parts.append(f"↗ +{analysis.trend_change_pct:.0f}%")
                elif "falling" in analysis.trend.lower():
                    # Falling players should be rare now, but mark them clearly
                    reason_parts.append(f"⚠️ {analysis.trend_change_pct:.0f}%")
            elif analysis.trend_change_pct is not None and abs(analysis.trend_change_pct) >= 3:
                # Show trend even without explicit trend label
                if analysis.trend_change_pct > 0:
                    reason_parts.append(f"↗ +{analysis.trend_change_pct:.0f}%")
                else:
                    reason_parts.append(f"↘ {analysis.trend_change_pct:.0f}%")

            # Add market discount if significant
            if analysis.value_change_pct >= 15:
                reason_parts.append(f"Cheap {analysis.value_change_pct:+.0f}%")

            # Add meaningful factors (skip "Base Value" - that's implicit)
            if hasattr(analysis, "factors") and analysis.factors:
                for factor in analysis.factors:
                    # Skip base value - we want to show WHY they're good
                    if "Base Value" in factor.name:
                        continue
                    if abs(factor.score) > 8:  # Lower threshold to catch more factors
                        # Shorten and clean up factor names
                        factor_name = (
                            factor.name.replace("Schedule Strength", "SOS")
                            .replace("Form Trajectory", "Form")
                            .replace("Rising Trend", "Rising")
                            .replace("Falling Trend", "Falling")
                            .replace("Market Discount", "Discount")
                        )
                        reason_parts.append(factor_name)
                        break

            # Show performance metrics if no other reasons
            if not reason_parts:
                if analysis.points_per_million >= 10:
                    reason_parts.append(f"{analysis.points_per_million:.1f} pts/M€")
                elif analysis.average_points >= 50:
                    reason_parts.append(f"{analysis.average_points:.0f} avg pts")

            reason = " | ".join(reason_parts[:2]) if reason_parts else "High value"

            table.add_row(
                name,  # Player name
                player.position[:2],  # Shorten position
                bid_str,
                f"{analysis.value_score:.0f}",
                sos_indicator,
                days_str,
                reason,
            )

        console.print(table)

    def _display_sell_table(self, sell_recommendations: list):
        """Display compact sell recommendations table"""
        table = Table(show_header=True, header_style="bold red", box=None)
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Pos", style="blue", width=3)
        table.add_column("Value", justify="right", style="yellow")
        table.add_column("P/L", justify="right", style="green")
        table.add_column("Peak", justify="right", style="dim")
        table.add_column("Why", style="dim")

        for analysis in sell_recommendations:
            player = analysis.player
            name = f"{player.first_name} {player.last_name}"

            # Profit/loss
            pl_pct = analysis.value_change_pct
            pl_color = "green" if pl_pct > 0 else "red"
            pl_str = f"[{pl_color}]{pl_pct:+.0f}%[/{pl_color}]"

            # Peak info
            peak_str = "-"
            if hasattr(analysis, "metadata") and analysis.metadata:
                if "decline_from_peak_pct" in analysis.metadata:
                    decline = analysis.metadata["decline_from_peak_pct"]
                    if decline > 1:
                        peak_str = f"[red]-{decline:.0f}%[/red]"

            # Compact reason
            reason_parts = []
            if pl_pct >= 30:
                reason_parts.append("Take profit")
            elif pl_pct <= -10:
                reason_parts.append("Cut loss")

            if analysis.trend and "falling" in analysis.trend:
                reason_parts.append("Declining")

            if hasattr(analysis, "metadata") and analysis.metadata:
                if analysis.metadata.get("is_declining"):
                    reason_parts.append("Off peak")

            # Add first factor
            if hasattr(analysis, "factors") and analysis.factors:
                for factor in analysis.factors:
                    if abs(factor.score) > 15:
                        reason_parts.append(factor.name)
                        break

            reason = " | ".join(reason_parts[:2]) if reason_parts else analysis.reason[:30]

            table.add_row(
                name,
                player.position[:2],
                f"€{analysis.market_value/1_000_000:.1f}M",
                pl_str,
                peak_str,
                reason,
            )

        console.print(table)

    def _display_flip_table(self, flip_opportunities: list):
        """Display compact flip opportunities table"""
        table = Table(show_header=True, header_style="bold yellow", box=None)
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Pos", style="blue", width=3)
        table.add_column("Smart Bid", justify="right", style="yellow")
        table.add_column("Profit", justify="right", style="green")
        table.add_column("Days", justify="center", width=5)
        table.add_column("Risk", justify="center", width=4)
        table.add_column("Why", style="dim")

        for opp in flip_opportunities:
            player = opp.player
            name = f"{player.first_name} {player.last_name}"

            # Calculate smart bid
            if self.bidding:
                bid_rec = self.bidding.calculate_bid(
                    asking_price=opp.buy_price,
                    market_value=opp.market_value,
                    value_score=60.0,
                    confidence=0.7,
                    is_replacement=False,
                    player_id=player.id,
                )
                bid_str = f"€{bid_rec.recommended_bid:,}\n+{bid_rec.overbid_pct:.0f}%"
            else:
                bid_str = f"€{opp.buy_price:,}"

            # Profit
            profit_color = "green" if opp.value_gap_pct > 20 else "yellow"
            profit_str = f"[{profit_color}]+{opp.value_gap_pct:.0f}%[/{profit_color}]"

            # Risk
            if opp.risk_score < 30:
                risk_str = "[green]Low[/green]"
            elif opp.risk_score < 60:
                risk_str = "[yellow]Med[/yellow]"
            else:
                risk_str = "[red]High[/red]"

            # Compact reason
            reason = opp.reason[:40] + "..." if len(opp.reason) > 40 else opp.reason

            table.add_row(
                name,
                player.position[:2],
                bid_str,
                profit_str,
                f"{opp.hold_days}d",
                risk_str,
                reason,
            )

        console.print(table)

    def _display_squad_summary(self, summary: dict):
        """Display compact squad summary"""
        budget = summary.get("budget", 0)
        team_value = summary.get("team_value", 0)
        available = summary.get("available_to_spend", 0)
        best_11_strength = summary.get("best_11_strength", 0)
        sell_count = summary.get("sell_count", 0)
        hold_count = summary.get("hold_count", 0)

        budget_color = "green" if budget > 0 else "red"

        console.print(
            f"\n[bold]Budget:[/bold] [{budget_color}]€{budget/1_000_000:.1f}M[/{budget_color}]",
            end=" | ",
        )
        console.print(f"[bold]Team Value:[/bold] €{team_value/1_000_000:.0f}M", end=" | ")
        console.print(f"[bold]Can Spend:[/bold] €{available/1_000_000:.1f}M")

        console.print(f"\n[bold]Best 11:[/bold] {best_11_strength:.0f} pts/week", end=" | ")
        console.print(f"[bold]Bench:[/bold] {sell_count} SELL, {hold_count} HOLD")

    def _display_market_insights(self, insights: dict):
        """Display brief market intelligence"""
        easy_schedule_count = insights.get("easy_schedule_count", 0)
        price_drop_soon = insights.get("price_drop_soon", 0)
        rising_trend_count = insights.get("rising_trend_count", 0)
        new_listings = insights.get("new_listings", 0)

        if easy_schedule_count > 0:
            console.print(
                f"\n• ⚡ [green]{easy_schedule_count} player(s) with easy schedules available[/green]"
            )

        if price_drop_soon > 0:
            console.print(
                f"• 📉 [yellow]{price_drop_soon} player(s) may drop price soon (5+ days listed)[/yellow]"
            )

        if rising_trend_count > 0:
            console.print(
                f"• 📈 [green]{rising_trend_count} player(s) with strong upward trends[/green]"
            )

        if new_listings > 0:
            console.print(f"• 🆕 [cyan]{new_listings} fresh listing(s) today (good timing)[/cyan]")

        console.print("\n• 🔍 [dim]Run 'rehoboam market-intel' for price adjustment patterns[/dim]")
