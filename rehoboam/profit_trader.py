"""Profit trading - Buy low, sell high to accumulate budget"""

import math
from dataclasses import dataclass

from rich.console import Console

console = Console()


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
        self,
        min_profit_pct: float = 10.0,
        max_hold_days: int = 7,
        max_risk_score: float = 50.0,
        max_overpay_pct: float = 1.0,
        require_rising_trend: bool = True,
        min_last_move_pct: float = 0.0,
        max_decel_ratio: float = 0.0,
    ):
        """
        Args:
            min_profit_pct: Minimum profit percentage to consider
            max_hold_days: Maximum days to hold before selling
            max_risk_score: Maximum risk score (0-100)
            max_overpay_pct: Most a flip may pay above market value. A flip is
                bought to be sold, so any premium at entry has to be earned
                back before the trade breaks even.
            require_rising_trend: Only flip a player whose value is already
                rising, rather than betting on mean reversion.
        """
        self.min_profit_pct = min_profit_pct
        self.max_hold_days = max_hold_days
        self.max_risk_score = max_risk_score
        self.max_overpay_pct = max_overpay_pct
        self.require_rising_trend = require_rising_trend
        # Entry strength (2026-10-07): the last nightly move must be at
        # least this, and at least this share of the move before it. Tietz
        # at +0.2% a night after +1.2% ten nights earlier was a run at its
        # end, not one to join. Both default to 0 = off.
        self.min_last_move_pct = float(min_last_move_pct)
        self.max_decel_ratio = float(max_decel_ratio)

    def _entry_price(self, player, forecast_pct: float | None) -> int:
        """What the flip bids: the overpay cap, lifted to the forecast value.

        Kickbase declines a bid that is below the player's market value when
        the listing expires, and the listing expires after the nightly
        update. A rising player's bid therefore has to clear tomorrow's
        value, not today's — at exactly market value the bot won only the
        players that fell overnight (2026-09-26 to 10-07: 17 bids, 6 won,
        five of them falling, all five sold at a loss).
        """
        capped = min(player.price, self._max_flip_price(player))
        if forecast_pct is None or forecast_pct <= 0:
            return capped
        at_expiry = int(math.ceil(player.market_value * (1.0 + forecast_pct / 100.0)))
        return max(capped, at_expiry)

    def _max_flip_price(self, player) -> int:
        """Ceiling on what a flip may pay: market value plus the allowed premium."""
        return int(player.market_value * (1.0 + self.max_overpay_pct / 100.0))

    def find_profit_opportunities(
        self,
        market_players: list,
        current_budget: int,
        player_trends: dict[str, dict],
        max_opportunities: int = 10,
        team_value: int = 0,
        max_debt_pct: float = 60.0,
        player_forecasts: dict[str, float] | None = None,
    ) -> list[ProfitOpportunity]:
        """
        Find players to buy and flip for profit

        Args:
            market_players: Players on market (KICKBASE only)
            current_budget: Available budget
            player_trends: Dict mapping player_id -> trend analysis
            max_opportunities: Max opportunities to return
            team_value: Total team value (for debt capacity calculation)
            max_debt_pct: Max debt as percentage of team value
            player_forecasts: player_id -> forecast move (percent) at the next
                market-value update. Two uses (2026-10-07): a negative one
                vetoes the flip like a negative last nightly move does, and a
                positive one is the entry premium the bid must clear, because
                Kickbase declines a bid below the market value at expiry.

        Returns:
            List of ProfitOpportunity sorted by best profit potential
        """
        opportunities = []
        checked = 0
        healthy = 0
        affordable = 0
        has_trend_data = 0
        not_rising_filtered = 0
        turned_filtered = 0
        entry_filtered = 0
        forecasts = player_forecasts or {}
        meets_threshold = 0
        small_sample_filtered = 0

        # Use debt capacity for affordability (can go negative to flip)
        max_debt = int(team_value * (max_debt_pct / 100)) if team_value > 0 else 0
        max_affordable = current_budget + max_debt

        for player in market_players:
            checked += 1

            # Skip injured/unavailable players
            if player.status != 0:
                continue

            healthy += 1

            # Must be affordable (including debt capacity)
            if player.price > max_affordable:
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
                # KICKBASE players: Look for momentum opportunities ONLY
                # ONLY bid on rising trend players with decent points

                # Filter 1: Must have minimum avg points (avoid 0-point players)
                MIN_AVG_POINTS = 20.0  # Don't flip players with <20 pts/week (relaxed from 30)
                if player.average_points < MIN_AVG_POINTS:
                    continue  # Skip low-performing players

                # Filter 2: CRITICAL - Detect and skip small sample size anomalies
                # Players like Emre Can (1 game, 100+ points) have:
                # - Very high average points (>80)
                # - Very strong rising trend (>40%) - market reacting to one game
                # - This is a small sample size trap!
                # HEURISTIC: If avg_points > 80 AND trend > 40%, likely 1-2 game anomaly
                if player.average_points > 80 and trend_pct > 40:
                    small_sample_filtered += 1
                    console.print(
                        f"[yellow]⚠️  Filtered {player.first_name} {player.last_name} from profit trades: "
                        f"Likely small sample size ({player.average_points:.1f} pts/game, +{trend_pct:.1f}% trend)[/yellow]"
                    )
                    continue  # Skip small sample size anomalies

                # Filter 3: Accept rising trends OR stable performers with good
                # points OR dip-in-uptrend (mean reversion).
                expected_appreciation = 0
                is_dip_in_uptrend = trend.get("is_dip_in_uptrend", False)
                is_secular_decline = trend.get("is_secular_decline", False)
                is_recovery = trend.get("is_recovery", False)

                if trend_direction == "rising" and trend_pct > 5:
                    # Expect trend to continue (cap at 20%)
                    expected_appreciation = min(trend_pct, 20)
                    # The rise must still be a rise tonight. Kömür and Wöber
                    # (2026-10-02) were +60% over 14 days and had already
                    # turned: -1.5%, then -8% and -9% the night before the
                    # bid. The 14-day window cannot see that; the last move
                    # and the forecast can.
                    last_move = trend.get("trend_1d_pct")
                    forecast = forecasts.get(player.id)
                    if (last_move is not None and last_move < 0) or (
                        forecast is not None and forecast < 0
                    ):
                        turned_filtered += 1
                        continue
                    if last_move is not None and last_move < self.min_last_move_pct:
                        turned_filtered += 1
                        continue
                    prev_move = trend.get("trend_1d_prev_pct")
                    if (
                        last_move is not None
                        and prev_move is not None
                        and prev_move > 0
                        and self.max_decel_ratio > 0
                        and last_move < self.max_decel_ratio * prev_move
                    ):
                        turned_filtered += 1  # the rise halved overnight: slowing to a stop
                        continue
                elif self.require_rising_trend:
                    # Every branch below is a bet that the market is wrong —
                    # mean reversion on a dip, a recovery, a stable performer,
                    # or a fallen high-scorer. Across 151 real flips those bets
                    # lost EUR 55.3M at a 28% win rate. A rising trend is the
                    # one case where the market is already moving our way.
                    not_rising_filtered += 1
                    continue
                elif is_recovery and player.average_points >= 30:
                    # Recovery signal: short-term reversal after dip → catch the bounce
                    expected_appreciation = 12
                elif is_dip_in_uptrend and player.average_points >= 30:
                    # Genuine dip in a longer uptrend — best mean-reversion play.
                    # Trend service already filtered for: recent down + medium up.
                    expected_appreciation = 10
                elif trend_direction == "stable" and player.average_points >= 40:
                    # Stable good performers - conservative 8% appreciation
                    expected_appreciation = 8
                elif trend_direction == "falling" and peak_value > 0:
                    # Hard mean reversion for high-quality players that crashed.
                    # Stricter threshold than dip-in-uptrend because a falling
                    # trend without uptrend context is riskier.
                    if is_secular_decline:
                        continue  # genuine decline, not a dip — avoid
                    current_vs_peak_pct = ((current_value - peak_value) / peak_value) * 100
                    if current_vs_peak_pct < -25 and player.average_points >= 40:
                        # >25% below peak + high performer + not in secular decline
                        expected_appreciation = min(abs(current_vs_peak_pct) * 0.3, 15)
                    else:
                        continue  # Not deep enough or not high enough quality
                else:
                    # Not a recognized profit pattern — skip
                    continue

                # The entry premium: a bid below the market value at expiry
                # is declined, and the listing expires after the update, so
                # the bid has to clear the forecast value. That premium is
                # paid out of the expected appreciation; if what is left is
                # under the minimum, there is no flip here.
                entry_pct = max(0.0, float(forecasts.get(player.id) or 0.0))
                if expected_appreciation - entry_pct < self.min_profit_pct:
                    if entry_pct > 0:
                        entry_filtered += 1
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
                    buy_price=self._entry_price(player, forecasts.get(player.id)),
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
