"""Profit trading - Buy low, sell high to accumulate budget"""

from dataclasses import dataclass


@dataclass
class ProfitOpportunity:
    """A player we can buy and flip for profit"""

    player: any  # MarketPlayer
    buy_price: int  # Current asking price
    market_value: int  # True market value
    value_gap: int  # market_value - buy_price (profit potential)
    value_gap_pct: float  # Percentage profit potential
    expected_appreciation: float  # Expected value increase based on trends
    risk_score: float  # 0-100, higher = riskier
    hold_days: int  # Recommended holding period
    reason: str  # Why this is a good flip


@dataclass
class FlipTrade:
    """A completed or active flip trade"""

    player_id: str
    player_name: str
    buy_price: int
    buy_date: float  # Unix timestamp
    sell_price: int | None = None  # None if still holding
    sell_date: float | None = None  # Unix timestamp
    profit: int | None = None  # Actual profit/loss
    profit_pct: float | None = None  # Percentage return
    hold_days: int | None = None  # Actual holding period
    status: str = "holding"  # holding, sold, target


class ProfitTrader:
    """Find and execute profit trading opportunities"""

    def __init__(
        self, min_profit_pct: float = 10.0, max_hold_days: int = 7, max_risk_score: float = 50.0
    ):
        """
        Args:
            min_profit_pct: Minimum profit percentage to consider
            max_hold_days: Maximum days to hold before selling
            max_risk_score: Maximum risk score (0-100)
        """
        self.min_profit_pct = min_profit_pct
        self.max_hold_days = max_hold_days
        self.max_risk_score = max_risk_score

    def find_profit_opportunities(
        self,
        market_players: list,
        current_budget: int,
        player_trends: dict[str, dict],
        max_opportunities: int = 10,  # Increased from 5 to 10 since we can use debt
    ) -> list[ProfitOpportunity]:
        """
        Find players to buy and flip for profit

        Args:
            market_players: Players on market (KICKBASE only)
            current_budget: Available budget
            player_trends: Dict mapping player_id -> trend analysis
            max_opportunities: Max opportunities to return

        Returns:
            List of ProfitOpportunity sorted by best profit potential
        """
        opportunities = []
        checked = 0
        affordable = 0
        has_trend_data = 0
        meets_threshold = 0

        for player in market_players:
            checked += 1
            # Must be affordable
            if player.price > current_budget:
                continue

            affordable += 1

            # Get trend data (required for KICKBASE players)
            trend = player_trends.get(player.id, {})
            if not trend.get("has_data", False):
                continue  # Skip players without trend data

            has_trend_data += 1

            trend_direction = trend.get("trend", "unknown")
            trend_pct = trend.get("trend_pct", 0)
            current_value = trend.get("current_value", player.market_value)
            peak_value = trend.get("peak_value", 0)

            # For KICKBASE players: price = market_value
            # Calculate profit potential from expected appreciation
            is_kickbase = player.price == player.market_value

            if is_kickbase:
                # KICKBASE players: Look for momentum opportunities
                # Must have rising trend OR be significantly below peak (but NOT falling)
                expected_appreciation = 0

                # Rising trend = momentum opportunity
                if trend_direction == "rising" and trend_pct > 5:
                    # Expect trend to continue (cap at 20%)
                    expected_appreciation = min(trend_pct, 20)

                # Below peak = mean reversion opportunity (only if NOT actively falling)
                elif peak_value > 0 and trend_direction != "falling":
                    vs_peak_pct = ((current_value - peak_value) / peak_value) * 100
                    if vs_peak_pct < -15:  # More than 15% below peak
                        # Expect partial recovery (50% of the gap)
                        recovery_potential = abs(vs_peak_pct) * 0.5
                        expected_appreciation = min(recovery_potential, 20)

                # Skip if no profit potential
                if expected_appreciation < self.min_profit_pct:
                    continue

                # Virtual "value gap" based on expected appreciation
                value_gap_pct = expected_appreciation
                value_gap = int((value_gap_pct / 100) * player.price)

            else:
                # Non-KICKBASE players: Traditional value gap approach
                value_gap = player.market_value - player.price
                if value_gap <= 0:
                    continue  # Not undervalued

                value_gap_pct = (value_gap / player.price) * 100

                # Must meet minimum profit threshold
                if value_gap_pct < self.min_profit_pct:
                    continue

                # Calculate expected appreciation from trends
                if trend_direction == "rising":
                    expected_appreciation = min(trend_pct, 20)
                elif trend_direction == "falling":
                    expected_appreciation = abs(trend_pct) * 0.5  # Mean reversion
                else:
                    expected_appreciation = 5  # Default modest growth

            meets_threshold += 1

            # Calculate risk score
            risk_score = self._calculate_risk(
                player=player, trend=trend, value_gap_pct=value_gap_pct
            )

            # Skip if too risky
            if risk_score > self.max_risk_score:
                continue

            # Estimate holding period
            # Higher profit potential = hold longer
            # Rising trend = hold longer
            if value_gap_pct > 20:
                hold_days = min(self.max_hold_days, 7)
            elif value_gap_pct > 15:
                hold_days = 5
            else:
                hold_days = 3

            if trend_direction == "rising":
                hold_days = min(hold_days + 2, self.max_hold_days)

            # Build reason
            reasons = []

            if is_kickbase:
                # KICKBASE opportunity reasons
                reasons.append(f"{value_gap_pct:.1f}% expected appreciation")

                if trend_direction == "rising":
                    reasons.append(f"Rising trend ({trend_pct:+.1f}%)")

                if peak_value > 0:
                    vs_peak_pct = ((current_value - peak_value) / peak_value) * 100
                    if vs_peak_pct < -15:
                        reasons.append(f"{abs(vs_peak_pct):.1f}% below peak")
            else:
                # Non-KICKBASE opportunity reasons
                reasons.append(f"{value_gap_pct:.1f}% undervalued")

                if trend_direction == "rising":
                    reasons.append(f"Rising trend ({trend_pct:+.1f}%)")
                elif trend_direction == "falling":
                    reasons.append("Mean reversion opportunity")

            if player.average_points > 50:
                reasons.append("High performer")

            reason = " | ".join(reasons)

            opportunities.append(
                ProfitOpportunity(
                    player=player,
                    buy_price=player.price,
                    market_value=player.market_value,
                    value_gap=value_gap,
                    value_gap_pct=value_gap_pct,
                    expected_appreciation=expected_appreciation,
                    risk_score=risk_score,
                    hold_days=hold_days,
                    reason=reason,
                )
            )

        # Sort by profit potential (value gap % + expected appreciation)
        opportunities.sort(key=lambda o: o.value_gap_pct + o.expected_appreciation, reverse=True)

        # Debug: Print filtering statistics
        print("\n[DEBUG] Profit Opportunity Filtering:")
        print(f"  Checked: {checked} players")
        print(f"  Affordable: {affordable} (budget limit)")
        print(f"  Has trend data: {has_trend_data} (92-day history)")
        print(f"  Meets threshold: {meets_threshold} (>= {self.min_profit_pct}% expected profit)")
        print(f"  Passed risk: {len(opportunities)} (risk <= {self.max_risk_score})")
        print(f"  Final opportunities: {min(len(opportunities), max_opportunities)}")

        return opportunities[:max_opportunities]

    def _calculate_risk(self, player: any, trend: dict, value_gap_pct: float) -> float:
        """
        Calculate risk score 0-100 (higher = riskier)

        Risk factors:
        - Falling value trend
        - Low points average
        - Very high value gap (might be error)
        - No trend data
        """
        risk = 0

        # Trend risk
        trend_direction = trend.get("trend", "unknown")
        if trend_direction == "falling":
            risk += 30
        elif trend_direction == "unknown":
            risk += 20

        # Performance risk
        if player.average_points < 20:
            risk += 25
        elif player.average_points < 40:
            risk += 15

        # Value gap risk (too good to be true?)
        if value_gap_pct > 50:
            risk += 20  # Suspiciously high
        elif value_gap_pct > 30:
            risk += 10

        # Position risk (some positions harder to sell)
        if player.position == "Goalkeeper":
            risk += 10  # Less liquid market

        return min(risk, 100)

    def should_sell_flip(
        self, flip: FlipTrade, current_value: int, days_held: int
    ) -> tuple[bool, str]:
        """
        Decide if we should sell a flip trade

        Returns:
            (should_sell, reason)
        """
        # Calculate current profit
        profit = current_value - flip.buy_price
        profit_pct = (profit / flip.buy_price) * 100

        # Sell conditions
        # 1. Hit profit target
        if profit_pct >= self.min_profit_pct:
            return True, f"Hit profit target: {profit_pct:.1f}%"

        # 2. Held too long
        if days_held >= self.max_hold_days:
            if profit > 0:
                return True, f"Max hold reached ({days_held}d) - take profit {profit_pct:.1f}%"
            else:
                return True, f"Max hold reached ({days_held}d) - cut losses {profit_pct:.1f}%"

        # 3. Stop loss (lost >5%)
        if profit_pct < -5:
            return True, f"Stop loss: {profit_pct:.1f}%"

        # 4. Small profit and held a while
        if profit_pct > 5 and days_held >= 3:
            return True, f"Take quick profit: {profit_pct:.1f}% after {days_held}d"

        return False, "Hold"

    def calculate_flip_budget_allocation(
        self, total_budget: int, reserve_for_lineup: int = 0
    ) -> int:
        """
        Calculate how much budget to allocate for profit trading

        Args:
            total_budget: Total available budget
            reserve_for_lineup: Budget to reserve for lineup improvements

        Returns:
            Budget available for profit trading
        """
        # Reserve budget for lineup improvements
        available = total_budget - reserve_for_lineup

        # Use max 50% of available for flips (keep liquidity)
        flip_budget = int(available * 0.5)

        return max(0, flip_budget)
