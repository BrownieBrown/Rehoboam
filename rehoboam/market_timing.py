"""Market timing intelligence for optimal buy/sell decisions"""

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

console = Console()


@dataclass
class MarketTiming:
    """Timing intelligence for trades"""

    player_id: str
    player_name: str

    # Urgency scoring
    buy_urgency: float  # 0-100, how soon to buy
    sell_urgency: float  # 0-100, how soon to sell

    # Timing signals
    price_acceleration: str  # "accelerating", "decelerating", "stable"
    momentum_change: float  # Change in rate of change (2nd derivative)
    volume_signal: str | None  # "high_volume", "low_volume", "normal"

    # Pattern detection
    detected_patterns: list[str]  # "pre_gameday_surge", "post_injury_dip", etc.

    # Recommendations
    optimal_action: str  # "buy_now", "wait_3_days", "sell_immediately", "hold"
    reason: str
    confidence: float


class MarketTimingAnalyzer:
    """Analyze optimal timing for trades"""

    def __init__(self):
        pass

    def analyze_timing(
        self,
        player_analysis,  # PlayerAnalysis
        price_history: list[int] | None = None,
        days_until_gameday: int | None = None,
    ) -> MarketTiming:
        """
        Analyze optimal timing for buying or selling a player

        Args:
            player_analysis: PlayerAnalysis object
            price_history: List of recent prices (newest first)
            days_until_gameday: Days until next match

        Returns:
            MarketTiming object
        """
        player = player_analysis.player
        player_name = f"{player.first_name} {player.last_name}"

        # Calculate price acceleration (2nd derivative)
        price_acceleration, momentum_change = self._calculate_acceleration(price_history)

        # Detect patterns
        detected_patterns = self._detect_patterns(
            player_analysis=player_analysis,
            price_history=price_history,
            days_until_gameday=days_until_gameday,
        )

        # Calculate urgency scores
        buy_urgency = self._calculate_buy_urgency(
            player_analysis=player_analysis,
            price_acceleration=price_acceleration,
            momentum_change=momentum_change,
            detected_patterns=detected_patterns,
            days_until_gameday=days_until_gameday,
        )

        sell_urgency = self._calculate_sell_urgency(
            player_analysis=player_analysis,
            price_acceleration=price_acceleration,
            momentum_change=momentum_change,
            detected_patterns=detected_patterns,
            days_until_gameday=days_until_gameday,
        )

        # Determine optimal action
        optimal_action, reason, confidence = self._determine_optimal_action(
            buy_urgency=buy_urgency,
            sell_urgency=sell_urgency,
            detected_patterns=detected_patterns,
            recommendation=player_analysis.recommendation,
        )

        # Volume signal (simplified - would need activity data)
        volume_signal = None

        return MarketTiming(
            player_id=player.id,
            player_name=player_name,
            buy_urgency=buy_urgency,
            sell_urgency=sell_urgency,
            price_acceleration=price_acceleration,
            momentum_change=momentum_change,
            volume_signal=volume_signal,
            detected_patterns=detected_patterns,
            optimal_action=optimal_action,
            reason=reason,
            confidence=confidence,
        )

    def _calculate_acceleration(self, price_history: list[int] | None) -> tuple[str, float]:
        """
        Calculate price acceleration (2nd derivative)

        Returns:
            (acceleration_label, momentum_change)
        """
        if not price_history or len(price_history) < 3:
            return "stable", 0.0

        # Calculate daily changes (1st derivative)
        changes = []
        for i in range(len(price_history) - 1):
            if price_history[i + 1] > 0:
                daily_change = (
                    (price_history[i] - price_history[i + 1]) / price_history[i + 1] * 100
                )
                changes.append(daily_change)

        if len(changes) < 2:
            return "stable", 0.0

        # Calculate change in changes (2nd derivative)
        # Compare recent change vs earlier change
        recent_change = sum(changes[:3]) / 3 if len(changes) >= 3 else changes[0]
        earlier_change = sum(changes[3:6]) / 3 if len(changes) >= 6 else sum(changes) / len(changes)

        momentum_change = recent_change - earlier_change

        # Classify acceleration
        if momentum_change > 2:
            acceleration = "accelerating"  # Price rising faster
        elif momentum_change < -2:
            acceleration = "decelerating"  # Price falling or slowing
        else:
            acceleration = "stable"

        return acceleration, momentum_change

    def _detect_patterns(
        self,
        player_analysis,
        price_history: list[int] | None,
        days_until_gameday: int | None,
    ) -> list[str]:
        """Detect market patterns"""
        patterns = []

        # Pre-gameday surge pattern
        if days_until_gameday is not None and 1 <= days_until_gameday <= 2:
            # Players typically rise 1-2 days before match
            if player_analysis.trend and "rising" in player_analysis.trend:
                patterns.append("pre_gameday_surge")

        # Peak exhaustion pattern
        if player_analysis.trend_change_pct and player_analysis.trend_change_pct > 20:
            # Rapid rise often followed by plateau/decline
            patterns.append("peak_exhaustion")

        # Recovery pattern
        if player_analysis.vs_peak_pct and player_analysis.vs_peak_pct < -30:
            # Far below peak with positive trend = recovery
            if player_analysis.trend and "rising" in player_analysis.trend:
                patterns.append("recovery_bounce")

        # Panic sell pattern
        if player_analysis.trend_change_pct and player_analysis.trend_change_pct < -15:
            # Sharp decline = panic selling (buy opportunity)
            patterns.append("panic_sell")

        # Injury recovery pattern
        # (Would need injury data - simplified here)
        # if injury_recovering:
        #     patterns.append("injury_recovery")

        return patterns

    def _calculate_buy_urgency(
        self,
        player_analysis,
        price_acceleration: str,
        momentum_change: float,
        detected_patterns: list[str],
        days_until_gameday: int | None,
    ) -> float:
        """
        Calculate buy urgency (0-100)

        Higher = buy sooner
        """
        urgency = 50.0  # Base urgency

        # Acceleration factor
        if price_acceleration == "accelerating" and momentum_change > 3:
            # Price rising fast and accelerating = high urgency
            urgency += 30
        elif price_acceleration == "accelerating":
            urgency += 20
        elif price_acceleration == "decelerating" and momentum_change < -3:
            # Price falling = opportunity window
            urgency += 15

        # Trend factor
        if player_analysis.trend:
            if "rising" in player_analysis.trend and player_analysis.trend_change_pct:
                if player_analysis.trend_change_pct > 15:
                    urgency += 20  # Strong uptrend
                elif player_analysis.trend_change_pct > 5:
                    urgency += 10

        # Pattern factor
        if "pre_gameday_surge" in detected_patterns:
            # Buy before gameday surge
            urgency += 25
        if "recovery_bounce" in detected_patterns:
            # Early in recovery = good entry point
            urgency += 20
        if "panic_sell" in detected_patterns:
            # Buy the dip
            urgency += 30

        # Gameday factor
        if days_until_gameday is not None:
            if days_until_gameday == 2:
                # Optimal buy window (before pre-gameday surge)
                urgency += 15
            elif days_until_gameday <= 1:
                # Too late, prices already surging
                urgency -= 10
            elif days_until_gameday >= 5:
                # Plenty of time
                urgency -= 5

        # Value score factor
        if player_analysis.value_score >= 75:
            # High value = high urgency
            urgency += 15
        elif player_analysis.value_score >= 60:
            urgency += 10

        return max(0, min(100, urgency))

    def _calculate_sell_urgency(
        self,
        player_analysis,
        price_acceleration: str,
        momentum_change: float,
        detected_patterns: list[str],
        days_until_gameday: int | None,
    ) -> float:
        """
        Calculate sell urgency (0-100)

        Higher = sell sooner
        """
        urgency = 50.0  # Base urgency

        # Acceleration factor
        if price_acceleration == "decelerating" and momentum_change < -3:
            # Price falling fast = high sell urgency
            urgency += 30
        elif price_acceleration == "decelerating":
            urgency += 20

        # Trend factor
        if player_analysis.trend:
            if "falling" in player_analysis.trend and player_analysis.trend_change_pct:
                if player_analysis.trend_change_pct < -15:
                    urgency += 30  # Strong downtrend
                elif player_analysis.trend_change_pct < -5:
                    urgency += 20

        # Pattern factor
        if "peak_exhaustion" in detected_patterns:
            # At peak after rapid rise = sell now
            urgency += 35
        if "pre_gameday_surge" in detected_patterns:
            # Sell during surge (high prices)
            urgency += 20

        # Gameday factor
        if days_until_gameday is not None:
            if days_until_gameday <= 1:
                # Gameday imminent - sell if you're going to
                urgency += 20
            elif days_until_gameday == 2:
                # Pre-gameday surge = good time to sell
                urgency += 15

        # Value score factor (inverse of buy)
        if player_analysis.value_score < 40:
            # Low value = sell urgency
            urgency += 20
        elif player_analysis.value_score < 50:
            urgency += 10

        # Recommendation alignment
        if player_analysis.recommendation == "SELL":
            urgency += 15

        return max(0, min(100, urgency))

    def _determine_optimal_action(
        self,
        buy_urgency: float,
        sell_urgency: float,
        detected_patterns: list[str],
        recommendation: str,
    ) -> tuple[str, str, float]:
        """
        Determine optimal action and timing

        Returns:
            (action, reason, confidence)
        """
        # If both low urgency, hold
        if buy_urgency < 40 and sell_urgency < 40:
            return "hold", "No urgent timing signals", 0.6

        # If sell urgency much higher
        if sell_urgency > buy_urgency + 20:
            if sell_urgency >= 80:
                return "sell_immediately", f"Very high sell urgency ({sell_urgency:.0f}/100)", 0.9
            elif sell_urgency >= 60:
                return "sell_soon", f"High sell urgency ({sell_urgency:.0f}/100)", 0.8
            else:
                return "consider_selling", f"Moderate sell urgency ({sell_urgency:.0f}/100)", 0.6

        # If buy urgency much higher
        if buy_urgency > sell_urgency + 20:
            if buy_urgency >= 80:
                return "buy_now", f"Very high buy urgency ({buy_urgency:.0f}/100)", 0.9
            elif buy_urgency >= 60:
                return "buy_soon", f"High buy urgency ({buy_urgency:.0f}/100)", 0.8
            else:
                return "consider_buying", f"Moderate buy urgency ({buy_urgency:.0f}/100)", 0.6

        # Similar urgency - default to recommendation
        if recommendation == "BUY":
            if "pre_gameday_surge" in detected_patterns:
                return "buy_soon", "Pre-gameday surge starting", 0.7
            return "buy_when_ready", "Good buying opportunity", 0.6
        elif recommendation == "SELL":
            if "peak_exhaustion" in detected_patterns:
                return "sell_soon", "At peak after surge", 0.8
            return "sell_when_ready", "Good selling opportunity", 0.6
        else:
            return "hold", "Mixed timing signals", 0.5

    def display_timing_analysis(self, timing: MarketTiming):
        """Display market timing analysis"""
        lines = []
        lines.append(f"[bold cyan]⏰ Market Timing: {timing.player_name}[/bold cyan]")
        lines.append("")

        # Urgency meters
        lines.append("[bold]Urgency Scores:[/bold]")

        buy_color = (
            "red" if timing.buy_urgency >= 80 else "yellow" if timing.buy_urgency >= 60 else "green"
        )
        sell_color = (
            "red"
            if timing.sell_urgency >= 80
            else "yellow" if timing.sell_urgency >= 60 else "green"
        )

        lines.append(f"  Buy Urgency:  [{buy_color}]{timing.buy_urgency:.0f}/100[/{buy_color}]")
        lines.append(f"  Sell Urgency: [{sell_color}]{timing.sell_urgency:.0f}/100[/{sell_color}]")

        # Signals
        lines.append("")
        lines.append("[bold]Market Signals:[/bold]")
        lines.append(f"  Price Acceleration: {timing.price_acceleration}")
        lines.append(f"  Momentum Change: {timing.momentum_change:+.1f}%")

        if timing.detected_patterns:
            lines.append("")
            lines.append("[bold]Detected Patterns:[/bold]")
            for pattern in timing.detected_patterns:
                pattern_display = pattern.replace("_", " ").title()
                lines.append(f"  • {pattern_display}")

        # Recommendation
        lines.append("")
        action_color = (
            "green"
            if "buy" in timing.optimal_action
            else "red" if "sell" in timing.optimal_action else "yellow"
        )
        lines.append(
            f"[bold]Optimal Action:[/bold] [{action_color}]{timing.optimal_action.replace('_', ' ').upper()}[/{action_color}]"
        )
        lines.append(f"[dim]{timing.reason} (confidence: {timing.confidence*100:.0f}%)[/dim]")

        panel = Panel("\n".join(lines), border_style="cyan", padding=(1, 2))

        console.print(panel)
