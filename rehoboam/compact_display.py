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
        sell_candidates: list | None = None,
        recovery_plan: list | None = None,
        current_budget: int = 0,
        track_record: dict | None = None,
        learned_weights_count: int = 0,
        watchlist: list | None = None,
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
            sell_candidates: All players ranked by expendability
            recovery_plan: Players to sell to reach 0 budget (if in deficit)
            current_budget: Current budget (for recovery display)
        """
        console.print("\n" + "═" * 80)
        console.print("[bold cyan]🎯  ACTION PLAN[/bold cyan]")
        console.print("═" * 80)

        # Budget negative warning — ZERO POINTS penalty at kickoff
        if current_budget < 0:
            deficit = abs(current_budget)
            console.print(
                f"\n[bold red on white]"
                f" 🚨 BUDGET NEGATIVE (-€{deficit / 1_000_000:.1f}M)"
                f" — ZERO POINTS AT KICKOFF IF NOT FIXED! "
                f"[/bold red on white]"
            )
            console.print("[red]→ Sell recommendations below to recover budget[/red]\n")

        # Position needs indicator
        position_needs = squad_summary.get("position_needs")
        if position_needs:
            needs_str = " | ".join(position_needs)
            console.print(f"[yellow]⚠️  Position gaps: {needs_str}[/yellow]")

        # Track record summary
        if track_record and track_record.get("has_data"):
            parts = []
            bt = track_record.get("buy_total", 0)
            bw = track_record.get("buy_wins", 0)
            if bt > 0:
                bwr = track_record.get("buy_win_rate", 0)
                avg_p = track_record.get("buy_avg_profit_pct", 0)
                parts.append(f"BUY accuracy: {bw}/{bt} profitable ({bwr:.0%}), avg {avg_p:+.1f}%")
            if learned_weights_count > 0:
                parts.append(f"{learned_weights_count} learned weights active")
            if parts:
                console.print(f"[dim]{' | '.join(parts)}[/dim]")

        console.print()

        # BUY NOW Section
        trade_pairs = []
        if buy_opportunities:
            if is_emergency:
                console.print(
                    f"[bold red]🚨 URGENT: FILL SQUAD ({len(buy_opportunities)} recommendations)[/bold red]"
                )
                console.print(
                    f"[yellow]Squad critically low ({squad_size}/11) - relaxed quality standards to fill roster[/yellow]\n"
                )
            elif squad_size >= 15:
                console.print(
                    f"[bold green]🔄 TRADE MOVES ({len(buy_opportunities[:5])} swaps)[/bold green]"
                )
                console.print("[dim]Squad full (15/15) — sell first, then buy[/dim]\n")
            else:
                console.print(
                    f"[bold green]🟢 BUY NOW ({len(buy_opportunities)} opportunities)[/bold green]"
                )
                console.print(
                    "[dim]Rising/stable, healthy, only very difficult schedules filtered out[/dim]\n"
                )
            if squad_size >= 15 and not is_emergency:
                trade_pairs = self._build_trade_pairs(buy_opportunities[:5], sell_candidates or [])
                self._display_trade_table(trade_pairs)
            else:
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

        # Budget summary after buy recommendations
        if buy_opportunities and current_budget != 0:
            if squad_size >= 15 and not is_emergency:
                # Trade mode: show net spend accounting for sell proceeds
                total_net = 0
                for pair in trade_pairs:
                    analysis = pair["buy"]
                    if self.bidding:
                        bid_rec = self.bidding.calculate_bid(
                            asking_price=analysis.current_price,
                            market_value=analysis.market_value,
                            value_score=analysis.value_score,
                            confidence=analysis.confidence,
                            player_id=analysis.player.id,
                        )
                        bid_amount = bid_rec.recommended_bid
                    else:
                        bid_amount = analysis.current_price
                    total_net += bid_amount - pair["sell_value"]
                remaining = current_budget - total_net
                console.print(
                    f"\n[dim]Net spend for all {len(trade_pairs)} trades: "
                    f"€{total_net / 1_000_000:.1f}M | "
                    f"Budget after: €{remaining / 1_000_000:+.1f}M[/dim]"
                )
            else:
                displayed = buy_opportunities[: 8 if is_emergency else 5]
                cumulative_cost = 0
                for analysis in displayed:
                    if self.bidding:
                        bid_rec = self.bidding.calculate_bid(
                            asking_price=analysis.current_price,
                            market_value=analysis.market_value,
                            value_score=analysis.value_score,
                            confidence=analysis.confidence,
                            player_id=analysis.player.id,
                        )
                        cumulative_cost += bid_rec.recommended_bid
                    else:
                        cumulative_cost += analysis.current_price
                remaining = current_budget - cumulative_cost
                console.print(
                    f"\n[dim]Budget if buying all {len(displayed)}: €{remaining / 1_000_000:+.1f}M remaining "
                    f"(spending €{cumulative_cost / 1_000_000:.1f}M)[/dim]"
                )

        # WATCHLIST Section (near-threshold players)
        if watchlist:
            console.print(
                f"\n[bold yellow]👀 WATCHLIST ({len(watchlist)} players near threshold)[/bold yellow]"
            )
            console.print("[dim]Score 40-49, could become buys soon[/dim]\n")
            self._display_watchlist(watchlist)

        # SELL NOW Section
        if sell_urgent:
            console.print("\n[bold red]🔴 SELL NOW (Urgent)[/bold red]\n")
            self._display_sell_table(sell_urgent)
        else:
            console.print("\n[bold green]✓ NO URGENT SELLS[/bold green]")
            console.print(
                "[dim]Your squad looks healthy - no players need immediate selling[/dim]\n"
            )

        # RECOMMENDED SELLS Section (ranked by expendability)
        if sell_candidates:
            self._display_sell_candidates(sell_candidates, recovery_plan, current_budget)

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
        table.add_column("7d", justify="right", width=5)
        table.add_column("Impact", justify="center", style="dim", width=12)
        table.add_column("Next 3", justify="center", style="dim", width=7)
        table.add_column("Why", style="dim")

        for analysis in opportunities:
            player = analysis.player
            name = f"{player.first_name} {player.last_name}"

            # NEW badge for fresh listings (< 24h)
            if hasattr(player, "listed_at") and player.listed_at:
                try:
                    from datetime import datetime

                    listed_dt = datetime.fromisoformat(player.listed_at.replace("Z", "+00:00"))
                    hours_listed = (
                        datetime.now(listed_dt.tzinfo) - listed_dt
                    ).total_seconds() / 3600
                    if hours_listed < 24:
                        name = f"🆕 {name}"
                except Exception:
                    pass

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

            # Roster impact indicator
            impact_str = "-"
            if hasattr(analysis, "roster_impact") and analysis.roster_impact:
                impact = analysis.roster_impact
                if impact.impact_type == "upgrade":
                    # Show upgrade with replaced player name and value gain
                    short_name = (
                        impact.replaces_player.split()[-1] if impact.replaces_player else "?"
                    )
                    impact_str = f"[green]↑ {short_name}\n+{impact.value_score_gain:.0f}[/green]"
                elif impact.impact_type == "fills_gap":
                    # Show that this fills a needed position
                    impact_str = "[cyan]+ fills gap[/cyan]"
                elif impact.impact_type == "depth":
                    impact_str = "[yellow]= depth[/yellow]"
                else:
                    impact_str = "[dim]= depth[/dim]"

            reason = self._build_compact_reason(analysis)

            # 7d prediction from metadata
            pred_str = "[dim]-[/dim]"
            if hasattr(analysis, "metadata") and analysis.metadata:
                pred_7d = analysis.metadata.get("prediction_7d_pct")
                if pred_7d is not None:
                    pred_color = "green" if pred_7d >= 0 else "red"
                    pred_str = f"[{pred_color}]{pred_7d:+.0f}%[/{pred_color}]"

            table.add_row(
                name,  # Player name
                player.position[:2],  # Shorten position
                bid_str,
                f"{analysis.value_score:.0f}",
                pred_str,
                impact_str,
                sos_indicator,
                reason,
            )

        console.print(table)

    def _build_compact_reason(self, analysis) -> str:
        """Build a compact reason string for a buy recommendation."""
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
                reason_parts.append(f"⚠️ {analysis.trend_change_pct:.0f}%")
        elif analysis.trend_change_pct is not None and abs(analysis.trend_change_pct) >= 3:
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
                if "Base Value" in factor.name:
                    continue
                if abs(factor.score) > 8:
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

        # Add demand signal from metadata
        demand_score = 0
        if hasattr(analysis, "metadata") and analysis.metadata:
            demand_score = analysis.metadata.get("demand_score", 0)
        if demand_score >= 70:
            reason_parts.append("Hot")
        elif demand_score >= 50:
            reason_parts.append("In demand")

        # Add timing hint based on schedule
        sos_rating_val = None
        if hasattr(analysis, "metadata") and analysis.metadata:
            sos_rating_val = analysis.metadata.get("sos_rating")
        if sos_rating_val in ["Very Easy", "Easy"]:
            reason_parts.append("Buy now")
        elif sos_rating_val in ["Difficult"] and analysis.value_score >= 60:
            reason_parts.append("Wait for dip?")

        return " | ".join(reason_parts[:3]) if reason_parts else "High value"

    def _build_trade_pairs(self, opportunities, sell_candidates):
        """Pair each buy with a sell when squad is full."""
        pairs = []
        used_sell_ids = set()

        for analysis in opportunities:
            sell_name = None
            sell_value = 0
            sell_candidate = None

            # Case 1: "upgrade" — natural swap from roster_impact
            if (
                analysis.roster_impact
                and analysis.roster_impact.impact_type == "upgrade"
                and analysis.roster_impact.replaces_player
            ):
                sell_name = analysis.roster_impact.replaces_player
                sell_value = analysis.current_price - analysis.roster_impact.net_cost
                # Find matching SellCandidate for protection info
                for c in sell_candidates:
                    full = f"{c.player.first_name} {c.player.last_name}"
                    if (
                        c.player.last_name in sell_name or full == sell_name
                    ) and c.player.id not in used_sell_ids:
                        sell_candidate = c
                        sell_value = c.market_value
                        break

            # Case 2: no natural swap — pick most expendable non-protected
            if sell_candidate is None:
                for c in sell_candidates:
                    if not c.is_protected and c.player.id not in used_sell_ids:
                        sell_candidate = c
                        sell_name = c.player.last_name
                        sell_value = c.market_value
                        break

            if sell_candidate:
                used_sell_ids.add(sell_candidate.player.id)

            pairs.append(
                {
                    "buy": analysis,
                    "sell_name": sell_name,
                    "sell_value": sell_value,
                    "sell_protected": (sell_candidate.is_protected if sell_candidate else False),
                }
            )
        return pairs

    def _display_trade_table(self, pairs):
        """Display trade moves table (sell -> buy) when squad is full."""
        table = Table(show_header=True, header_style="bold green", box=None)
        table.add_column("Sell", style="red", no_wrap=True, width=14)
        table.add_column("", style="dim", width=2)
        table.add_column("Buy", style="cyan", no_wrap=True)
        table.add_column("Pos", style="blue", width=3)
        table.add_column("Smart Bid", justify="right", style="yellow")
        table.add_column("Score", justify="right", style="magenta", width=5)
        table.add_column("Net", justify="right", width=8)
        table.add_column("Why", style="dim")

        for pair in pairs:
            analysis = pair["buy"]
            player = analysis.player
            name = f"{player.first_name} {player.last_name}"

            # NEW badge for fresh listings (< 24h)
            if hasattr(player, "listed_at") and player.listed_at:
                try:
                    from datetime import datetime

                    listed_dt = datetime.fromisoformat(player.listed_at.replace("Z", "+00:00"))
                    hours_listed = (
                        datetime.now(listed_dt.tzinfo) - listed_dt
                    ).total_seconds() / 3600
                    if hours_listed < 24:
                        name = f"🆕 {name}"
                except Exception:
                    pass

            # Calculate smart bid
            if self.bidding:
                bid_rec = self.bidding.calculate_bid(
                    asking_price=analysis.current_price,
                    market_value=analysis.market_value,
                    value_score=analysis.value_score,
                    confidence=analysis.confidence,
                    player_id=player.id,
                )
                bid_amount = bid_rec.recommended_bid
                bid_str = f"€{bid_amount:,}\n+{bid_rec.overbid_pct:.0f}%"
            else:
                bid_amount = analysis.current_price
                bid_str = f"€{bid_amount:,}"

            # Sell column
            sell_name = pair["sell_name"]
            sell_value = pair["sell_value"]
            if sell_name:
                # Show last name only + value on second line
                short_sell = sell_name.split()[-1] if sell_name else "?"
                sell_str = f"{short_sell}\n(€{sell_value / 1_000_000:.1f}M)"
            else:
                sell_str = "[yellow]⚠️ No sell[/yellow]"

            # Net cost
            net = bid_amount - sell_value
            if net <= 0:
                net_str = f"[green]€{net / 1_000_000:+.1f}M[/green]"
            else:
                net_str = f"[red]€{net / 1_000_000:+.1f}M[/red]"

            # Reason
            reason = self._build_compact_reason(analysis)

            # Upgrade annotation
            if analysis.roster_impact and analysis.roster_impact.impact_type == "upgrade":
                reason += f" | Upgrade +{analysis.roster_impact.value_score_gain:.0f}"

            table.add_row(
                sell_str,
                "→",
                name,
                player.position[:2],
                bid_str,
                f"{analysis.value_score:.0f}",
                net_str,
                reason,
            )

        console.print(table)

    def _display_watchlist(self, watchlist: list):
        """Display near-threshold players that could become buys"""
        table = Table(show_header=True, header_style="bold yellow", box=None)
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Pos", style="blue", width=3)
        table.add_column("Price", justify="right", style="yellow")
        table.add_column("Score", justify="right", style="magenta", width=5)
        table.add_column("Why to Watch", style="dim")

        for analysis in watchlist[:3]:
            player = analysis.player
            name = f"{player.first_name} {player.last_name}"

            # Build watch reason
            reasons = []
            if analysis.trend and "rising" in analysis.trend.lower():
                reasons.append(f"Trending up +{analysis.trend_change_pct:.0f}%")
            if hasattr(analysis, "metadata") and analysis.metadata:
                sos = analysis.metadata.get("sos_rating")
                if sos in ["Easy", "Very Easy"]:
                    reasons.append("Easy schedule")
            if analysis.value_score >= 48:
                reasons.append("Almost at threshold")

            reason = " | ".join(reasons) if reasons else "Close to buy threshold"

            table.add_row(
                name,
                player.position[:2],
                f"€{analysis.current_price / 1_000_000:.1f}M",
                f"{analysis.value_score:.0f}",
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

        console.print(
            f"\n[bold]Best 11:[/bold] {best_11_strength:.0f} exp pts (next matchday)",
            end=" | ",
        )
        console.print(f"[bold]Bench:[/bold] {sell_count} SELL, {hold_count} HOLD")

        # Display best 11 lineup if available
        best_eleven = summary.get("best_eleven")
        player_values = summary.get("player_values", {})
        ep_map = summary.get("ep_map", {})
        dgw_player_ids = summary.get("dgw_player_ids", set())
        if best_eleven:
            self._display_best_eleven(best_eleven, player_values, ep_map, dgw_player_ids)

    def _display_best_eleven(
        self,
        best_eleven: list,
        player_values: dict,
        ep_map: dict | None = None,
        dgw_player_ids: set | None = None,
    ):
        """Display the best 11 starting lineup with expected points and DGW badges"""
        console.print("\n[bold]⭐ BEST 11 STARTING LINEUP[/bold]\n")

        ep_map = ep_map or {}
        dgw_player_ids = dgw_player_ids or set()

        table = Table(show_header=True, header_style="bold cyan", box=None)
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Pos", style="blue", width=3)
        table.add_column("Avg Pts", justify="right", style="yellow", width=7)
        table.add_column("Exp Pts", justify="right", style="green", width=7)
        table.add_column("Lineup", justify="center", width=8)
        table.add_column("Notes", style="dim")

        # Sort by position for display (GK, DEF, MID, FWD)
        position_order = {"Goalkeeper": 0, "Defender": 1, "Midfielder": 2, "Forward": 3}
        sorted_eleven = sorted(best_eleven, key=lambda p: position_order.get(p.position, 99))

        for player in sorted_eleven:
            name = f"{player.first_name} {player.last_name}"

            # DGW badge
            if player.id in dgw_player_ids:
                name = f"2x {name}"

            ep = ep_map.get(player.id)
            if ep:
                exp_pts_str = f"{ep.expected_points:.0f}"

                # Lineup probability display
                prob = ep.lineup_probability
                if prob == 1:
                    lineup_str = "[green]Starter[/green]"
                elif prob == 2:
                    lineup_str = "[yellow]Rotation[/yellow]"
                elif prob == 3:
                    lineup_str = "[yellow]Bench[/yellow]"
                elif prob is not None and prob >= 4:
                    lineup_str = "[red]Unlikely[/red]"
                else:
                    lineup_str = "[dim]-[/dim]"

                notes = " | ".join(ep.notes[:3]) if ep.notes else ""
            else:
                exp_pts_str = "[dim]-[/dim]"
                lineup_str = "[dim]-[/dim]"
                notes = ""

            table.add_row(
                name,
                player.position[:2],
                f"{player.average_points:.0f}",
                exp_pts_str,
                lineup_str,
                notes,
            )

        console.print(table)

    def _display_market_insights(self, insights: dict):
        """Display brief market intelligence"""
        easy_schedule_count = insights.get("easy_schedule_count", 0)
        price_drop_soon = insights.get("price_drop_soon", 0)
        rising_trend_count = insights.get("rising_trend_count", 0)
        new_listings = insights.get("new_listings", 0)
        injured_watch_count = insights.get("injured_watch_count", 0)

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

        if injured_watch_count > 0:
            console.print(
                f"• 🏥 [yellow]{injured_watch_count} injured player(s) on market — monitor for recovery bargains[/yellow]"
            )

        console.print("\n• 🔍 [dim]Run 'rehoboam market-intel' for price adjustment patterns[/dim]")

    def _display_sell_candidates(
        self, candidates: list, recovery_plan: list | None, current_budget: int
    ):
        """
        Display sell candidates ranked by expendability.

        Args:
            candidates: List of SellCandidate objects sorted by expendability
            recovery_plan: List of candidates to sell to recover budget (if in deficit)
            current_budget: Current budget for recovery calculation
        """
        console.print("\n" + "═" * 80)
        console.print("[bold cyan]🔻 RECOMMENDED SELLS (ranked by expendability)[/bold cyan]")
        console.print("═" * 80)
        console.print(
            "[dim]Higher expendability = safer to sell. Protected players should be kept.[/dim]\n"
        )

        # Check if all players are protected
        sellable = [c for c in candidates if not c.is_protected]
        if not sellable:
            console.print(
                "[yellow]⚠️  All players are protected - consider buying depth first[/yellow]\n"
            )

        # Display table of candidates
        table = Table(show_header=True, header_style="bold magenta", box=None)
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Pos", style="blue", width=3)
        table.add_column("Value", justify="right", style="magenta", width=5)
        table.add_column("P/L", justify="right", width=6)
        table.add_column("SOS", justify="center", width=7)
        table.add_column("Trend", justify="center", width=6)
        table.add_column("Timing", justify="center", width=10)
        table.add_column("Status", justify="center", width=12)

        # Show top candidates (limit to 8)
        for candidate in candidates[:8]:
            player = candidate.player
            name = f"{player.first_name} {player.last_name}"

            # Profit/loss coloring
            pl_pct = candidate.profit_loss_pct
            if pl_pct > 10:
                pl_str = f"[green]+{pl_pct:.0f}%[/green]"
            elif pl_pct > 0:
                pl_str = f"[green]+{pl_pct:.0f}%[/green]"
            elif pl_pct > -5:
                pl_str = f"[yellow]{pl_pct:.0f}%[/yellow]"
            else:
                pl_str = f"[red]{pl_pct:.0f}%[/red]"

            # SOS display (abbreviated)
            sos_display = "-"
            if candidate.sos_rating:
                if candidate.sos_rating == "Very Difficult":
                    sos_display = "[red]V.Hard[/red]"
                elif candidate.sos_rating == "Difficult":
                    sos_display = "[yellow]Hard[/yellow]"
                elif candidate.sos_rating == "Easy":
                    sos_display = "[green]Easy[/green]"
                elif candidate.sos_rating == "Very Easy":
                    sos_display = "[green]V.Easy[/green]"
                else:
                    sos_display = "Med"

            # Trend display
            trend_display = "-"
            if candidate.trend:
                if candidate.trend == "rising":
                    trend_display = "[green]↗[/green]"
                elif candidate.trend == "falling":
                    trend_display = "[red]↘[/red]"
                elif candidate.trend == "stable":
                    trend_display = "[yellow]→[/yellow]"

            # Timing signal - actionable sell timing advice
            timing_display = "-"
            pl_pct_val = candidate.profit_loss_pct
            trend_val = candidate.trend
            sos_val = candidate.sos_rating

            if trend_val == "falling" and pl_pct_val > 10:
                timing_display = "[red]Sell now[/red]"
            elif trend_val == "rising" and pl_pct_val > 20:
                timing_display = "[yellow]Near peak[/yellow]"
            elif sos_val in ["Very Difficult", "Difficult"] and pl_pct_val > 5:
                timing_display = "[yellow]Pre-fixture[/yellow]"
            elif trend_val == "rising" and pl_pct_val < 5:
                timing_display = "[green]Wait[/green]"
            elif candidate.recovery_signal == "CUT":
                timing_display = "[red]CUT[/red]"
            elif candidate.recovery_signal == "HOLD":
                timing_display = "[green]HOLD[/green]"

            # Status based on protection
            if candidate.is_protected:
                if "Only" in (candidate.protection_reason or ""):
                    status_str = f"[red]🔒 {candidate.protection_reason}[/red]"
                elif "Min" in (candidate.protection_reason or ""):
                    status_str = f"[yellow]⚠️ {candidate.protection_reason}[/yellow]"
                elif "Best 11" in (candidate.protection_reason or ""):
                    status_str = "[yellow]⚠️ Best 11[/yellow]"
                else:
                    status_str = f"[yellow]⚠️ {candidate.protection_reason}[/yellow]"
            else:
                status_str = "[green]SELL[/green]"

            table.add_row(
                name,
                player.position[:2],
                f"{candidate.value_score:.0f}",
                pl_str,
                sos_display,
                trend_display,
                timing_display,
                status_str,
            )

        console.print(table)

        # Show recovery plan if in deficit
        if current_budget < 0 and recovery_plan:
            console.print(
                f"\n[bold red]⚠️  BUDGET RECOVERY NEEDED: {current_budget / 1_000_000:+.1f}M[/bold red]"
            )

            running_budget = current_budget
            for candidate in recovery_plan:
                player = candidate.player
                name = f"{player.first_name} {player.last_name}"
                running_budget += candidate.market_value
                budget_str = f"{running_budget / 1_000_000:+.1f}M"

                if running_budget >= 0:
                    console.print(
                        f"   → Sell {name} ({candidate.market_value / 1_000_000:.1f}M) to reach {budget_str} [green]✓[/green]"
                    )
                else:
                    console.print(
                        f"   → Sell {name} ({candidate.market_value / 1_000_000:.1f}M) to reach {budget_str}"
                    )

            # Check if recovery plan is sufficient
            total_recovery = sum(c.market_value for c in recovery_plan)
            if current_budget + total_recovery < 0:
                console.print(
                    "\n[red]⚠️  Cannot reach 0 budget without selling Best 11 players![/red]"
                )
                # Calculate how much more is needed
                shortfall = abs(current_budget + total_recovery)
                console.print(
                    f"[yellow]   Shortfall: {shortfall / 1_000_000:.1f}M - consider selling protected players[/yellow]"
                )
        elif current_budget < 0:
            console.print(
                f"\n[bold red]⚠️  BUDGET RECOVERY NEEDED: {current_budget / 1_000_000:+.1f}M[/bold red]"
            )
            console.print(
                "[red]   No sellable players available! Must sell protected players.[/red]"
            )
        else:
            # Show tip about selling
            if sellable:
                # Calculate what selling top candidates would free up
                top_3_recovery = sum(c.market_value for c in sellable[:3])
                if top_3_recovery > 0:
                    console.print(
                        f"\n[dim]💡 Tip: Selling top 3 expendable players frees {top_3_recovery / 1_000_000:.1f}M budget[/dim]"
                    )

    def display_lineup(self, best_eleven: list, bench: list, expected_points_map: dict):
        """
        Display the optimal starting 11 for next matchday based on expected points.

        Args:
            best_eleven: List of players in the starting 11
            bench: List of bench players
            expected_points_map: Dict mapping player_id -> ExpectedPointsResult
        """
        console.print("\n" + "═" * 80)
        console.print("[bold cyan]⚽  MATCHDAY LINEUP (by Expected Points)[/bold cyan]")
        console.print("═" * 80)

        # Starting 11 table
        table = Table(show_header=True, header_style="bold green", box=None)
        table.add_column("#", style="dim", width=3)
        table.add_column("Player", style="cyan", no_wrap=True)
        table.add_column("Pos", style="blue", width=3)
        table.add_column("Avg Pts", justify="right", style="yellow", width=7)
        table.add_column("Exp Pts", justify="right", style="green", width=7)
        table.add_column("Lineup", justify="center", width=8)
        table.add_column("Notes", style="dim")

        # Sort by position for display (GK, DEF, MID, FWD)
        position_order = {"Goalkeeper": 0, "Defender": 1, "Midfielder": 2, "Forward": 3}
        sorted_eleven = sorted(best_eleven, key=lambda p: position_order.get(p.position, 99))

        total_expected = 0.0
        concerns = []

        for i, player in enumerate(sorted_eleven, 1):
            name = f"{player.first_name} {player.last_name}"
            ep = expected_points_map.get(player.id)

            if ep:
                exp_pts = ep.expected_points
                total_expected += exp_pts

                # Lineup probability display
                prob = ep.lineup_probability
                if prob == 1:
                    lineup_str = "[green]Starter[/green]"
                elif prob == 2:
                    lineup_str = "[yellow]Rotation[/yellow]"
                elif prob == 3:
                    lineup_str = "[yellow]Bench[/yellow]"
                    concerns.append(f"{name}: bench risk")
                elif prob is not None and prob >= 4:
                    lineup_str = "[red]Unlikely[/red]"
                    concerns.append(f"{name}: unlikely to play!")
                else:
                    lineup_str = "[dim]-[/dim]"

                notes = " | ".join(ep.notes[:2]) if ep.notes else ""

                table.add_row(
                    str(i),
                    name,
                    player.position[:2],
                    f"{player.average_points:.0f}",
                    f"{exp_pts:.0f}",
                    lineup_str,
                    notes,
                )
            else:
                table.add_row(
                    str(i),
                    name,
                    player.position[:2],
                    f"{player.average_points:.0f}",
                    "[dim]-[/dim]",
                    "[dim]-[/dim]",
                    "",
                )

        console.print("\n[bold]⭐ STARTING 11[/bold]\n")
        console.print(table)
        console.print(f"\n[bold]Total Expected Points: [green]{total_expected:.0f}[/green][/bold]")

        # Concerns
        if concerns:
            console.print("\n[yellow]⚠️  Concerns:[/yellow]")
            for concern in concerns:
                console.print(f"  • [yellow]{concern}[/yellow]")

        # Bench
        if bench:
            console.print("\n[bold]📋 BENCH[/bold] (ranked by expected points)\n")

            bench_table = Table(show_header=True, header_style="bold dim", box=None)
            bench_table.add_column("Player", style="dim cyan", no_wrap=True)
            bench_table.add_column("Pos", style="dim blue", width=3)
            bench_table.add_column("Avg Pts", justify="right", style="dim yellow", width=7)
            bench_table.add_column("Exp Pts", justify="right", style="dim", width=7)
            bench_table.add_column("Notes", style="dim")

            # Sort bench by expected points
            bench_sorted = sorted(
                bench,
                key=lambda p: (
                    expected_points_map.get(
                        p.id, type("", (), {"expected_points": 0})()
                    ).expected_points
                    if expected_points_map.get(p.id)
                    else 0
                ),
                reverse=True,
            )

            for player in bench_sorted:
                name = f"{player.first_name} {player.last_name}"
                ep = expected_points_map.get(player.id)
                exp_str = f"{ep.expected_points:.0f}" if ep else "-"
                notes = " | ".join(ep.notes[:2]) if ep and ep.notes else ""

                bench_table.add_row(
                    name,
                    player.position[:2],
                    f"{player.average_points:.0f}",
                    exp_str,
                    notes,
                )

            console.print(bench_table)

        console.print("\n" + "═" * 80)
        console.print(
            "[dim]Expected points based on avg performance, form, fixtures, and lineup probability[/dim]"
        )
        console.print("═" * 80 + "\n")
