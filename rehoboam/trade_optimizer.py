"""Trade optimization - Find best N-for-M player trades to improve starting 11"""

from dataclasses import dataclass
from itertools import combinations

from .formation import select_best_eleven, validate_trade


@dataclass
class TradeRecommendation:
    """Recommendation for an N-for-M trade"""

    players_out: list  # Players to sell
    players_in: list  # Players to buy
    improvement_points: float  # Expected points improvement per week
    improvement_value: float  # Value score improvement
    total_cost: int  # Cost of players_in (using smart bid prices)
    total_proceeds: int  # Proceeds from players_out
    net_cost: int  # total_cost - total_proceeds (negative = profit)
    required_budget: int  # Budget needed upfront (buy first, then sell)
    strategy: str  # "1-for-1", "2-for-2", etc.
    smart_bids: dict[str, int] = None  # Maps player.id -> recommended_bid amount


class TradeOptimizer:
    """Find optimal N-for-M trades to improve the starting 11"""

    def __init__(self, max_players_out: int = 3, max_players_in: int = 3, bidding_strategy=None):
        """
        Args:
            max_players_out: Maximum players to sell in one trade
            max_players_in: Maximum players to buy in one trade
            bidding_strategy: SmartBidding instance for calculating bid prices
        """
        self.max_players_out = max_players_out
        self.max_players_in = max_players_in
        self.bidding_strategy = bidding_strategy

    def calculate_lineup_strength(
        self, players: list, player_values: dict[str, float]
    ) -> tuple[float, float]:
        """
        Calculate total strength of a lineup

        Returns:
            (total_points, total_value_score)
        """
        total_points = sum(p.average_points for p in players)
        total_value = sum(player_values.get(p.id, 0) for p in players)

        return total_points, total_value

    def find_best_trades(
        self,
        current_squad: list,
        market_players: list,
        player_values: dict[str, float],
        current_budget: int,
        min_improvement_points: float = 2.0,
        min_improvement_value: float = 10.0,
        max_squad_size: int = 15,
    ) -> list[TradeRecommendation]:
        """
        Find all viable N-for-M trades that improve the starting 11
        Supports 0-for-M (buy without selling) if squad has room

        Args:
            current_squad: Your current squad
            market_players: Players available on market
            player_values: Dict mapping player.id -> value_score
            current_budget: Available budget
            min_improvement_points: Minimum points improvement required
            min_improvement_value: Minimum value score improvement required
            max_squad_size: Maximum squad size (default 15)

        Returns:
            List of TradeRecommendation sorted by improvement (best first)
        """
        # Select current best 11
        current_eleven = select_best_eleven(current_squad, player_values)
        current_points, current_value = self.calculate_lineup_strength(
            current_eleven, player_values
        )

        recommendations = []
        current_squad_size = len(current_squad)

        # Try different N-for-M combinations
        # Start at 0 to support buying without selling (0-for-M)
        for n_out in range(0, self.max_players_out + 1):
            for m_in in range(1, self.max_players_in + 1):
                # Check squad size limits
                new_squad_size = current_squad_size - n_out + m_in
                if new_squad_size > max_squad_size:
                    continue  # Would exceed max squad size

                # If n_out = 0, we're just buying (no selling)
                if n_out == 0:
                    # Just add players without removing any
                    self._evaluate_buy_only_trade(
                        current_squad=current_squad,
                        current_eleven=current_eleven,
                        current_points=current_points,
                        current_value=current_value,
                        market_players=market_players,
                        player_values=player_values,
                        current_budget=current_budget,
                        m_in=m_in,
                        min_improvement_points=min_improvement_points,
                        min_improvement_value=min_improvement_value,
                        recommendations=recommendations,
                    )
                else:
                    # Traditional N-for-M swap
                    # Generate all combinations of N players to sell from current eleven
                    for players_out in combinations(current_eleven, n_out):
                        # Generate all combinations of M players to buy from market
                        for players_in in combinations(market_players, m_in):
                            trade = self._evaluate_trade(
                                current_squad=current_squad,
                                current_eleven=current_eleven,
                                players_out=list(players_out),
                                players_in=list(players_in),
                                player_values=player_values,
                                current_budget=current_budget,
                                current_points=current_points,
                                current_value=current_value,
                                min_improvement_points=min_improvement_points,
                                min_improvement_value=min_improvement_value,
                            )

                            if trade:
                                recommendations.append(trade)

        # Sort by combined improvement (points + value + starter quality)
        def calculate_trade_score(trade: TradeRecommendation) -> float:
            # Base score: points improvement (1.0x) + value improvement (0.1x)
            score = trade.improvement_points + (trade.improvement_value / 10)

            # Starter quality bonus: prioritize acquiring high-average players
            # (likely starters on good teams who can carry to overall win)
            if trade.players_in:
                avg_quality = sum(p.average_points for p in trade.players_in) / len(
                    trade.players_in
                )
                # Add bonus for high-quality players (0-2 point bonus)
                if avg_quality > 50:  # Elite players
                    score += 2.0
                elif avg_quality > 40:  # Very good players
                    score += 1.5
                elif avg_quality > 30:  # Good players
                    score += 1.0
                elif avg_quality > 20:  # Decent players
                    score += 0.5

            return score

        recommendations.sort(key=calculate_trade_score, reverse=True)

        return recommendations

    def _evaluate_trade(
        self,
        current_squad: list,
        current_eleven: list,
        players_out: list,
        players_in: list,
        player_values: dict[str, float],
        current_budget: int,
        current_points: float,
        current_value: float,
        min_improvement_points: float,
        min_improvement_value: float,
    ) -> TradeRecommendation | None:
        """
        Evaluate a specific N-for-M trade

        Returns:
            TradeRecommendation if trade is viable, None otherwise
        """
        # Validate trade (formation requirements)
        validation = validate_trade(current_squad, players_out, players_in)
        if not validation["valid"]:
            return None

        # Calculate costs using smart bid prices
        smart_bids = {}
        if self.bidding_strategy:
            # Calculate smart bid for each incoming player
            for p in players_in:
                value_score = player_values.get(p.id, 0)
                # Estimate predicted future value (higher value score = more growth)
                growth_factor = 1.0 + (value_score / 1000)
                predicted_future_value = int(p.market_value * growth_factor)

                bid_rec = self.bidding_strategy.calculate_bid(
                    asking_price=p.price,
                    market_value=p.market_value,
                    value_score=value_score,
                    confidence=0.8,  # Conservative confidence for lineup trades
                    is_long_term_hold=True,  # LINEUP TRADES = long-term holds
                    average_points=p.average_points,  # Enable elite bidding for >70 pts
                    predicted_future_value=predicted_future_value,
                )
                smart_bids[p.id] = bid_rec.recommended_bid
            total_cost = sum(smart_bids.values())
        else:
            # Fallback to market value if no bidding strategy
            total_cost = sum(p.market_value for p in players_in)

        total_proceeds = sum(p.market_value for p in players_out)
        net_cost = total_cost - total_proceeds
        required_budget = total_cost  # Need full cost upfront (buy first)

        # Check budget constraint
        if required_budget > current_budget:
            return None  # Cannot afford

        # Simulate new squad after trade
        player_out_ids = {p.id for p in players_out}
        new_squad = [p for p in current_squad if p.id not in player_out_ids]
        new_squad.extend(players_in)

        # Select new best 11 from new squad
        new_eleven = select_best_eleven(new_squad, player_values)
        new_points, new_value = self.calculate_lineup_strength(new_eleven, player_values)

        # Calculate improvements
        improvement_points = new_points - current_points
        improvement_value = new_value - current_value

        # Check if improvement meets threshold
        if (
            improvement_points < min_improvement_points
            and improvement_value < min_improvement_value
        ):
            return None  # Not worth it

        # Create recommendation
        strategy = f"{len(players_out)}-for-{len(players_in)}"

        return TradeRecommendation(
            players_out=players_out,
            players_in=players_in,
            improvement_points=improvement_points,
            improvement_value=improvement_value,
            total_cost=total_cost,
            total_proceeds=total_proceeds,
            net_cost=net_cost,
            required_budget=required_budget,
            strategy=strategy,
            smart_bids=smart_bids if smart_bids else None,
        )

    def _evaluate_buy_only_trade(
        self,
        current_squad: list,
        current_eleven: list,
        current_points: float,
        current_value: float,
        market_players: list,
        player_values: dict[str, float],
        current_budget: int,
        m_in: int,
        min_improvement_points: float,
        min_improvement_value: float,
        recommendations: list[TradeRecommendation],
    ) -> None:
        """
        Evaluate buy-only trades (0-for-M): buying M players without selling any
        Adds viable trades to the recommendations list

        Args:
            current_squad: Current squad
            current_eleven: Current best 11
            current_points: Current lineup points
            current_value: Current lineup value
            market_players: Available market players
            player_values: Dict mapping player.id -> value_score
            current_budget: Available budget
            m_in: Number of players to buy
            min_improvement_points: Minimum points improvement required
            min_improvement_value: Minimum value improvement required
            recommendations: List to append viable trades to
        """
        # Generate all combinations of M players to buy from market
        for players_in in combinations(market_players, m_in):
            # Calculate costs using smart bid prices
            smart_bids = {}
            if self.bidding_strategy:
                # Calculate smart bid for each incoming player
                for p in players_in:
                    value_score = player_values.get(p.id, 0)
                    # Estimate predicted future value (higher value score = more growth)
                    growth_factor = 1.0 + (value_score / 1000)
                    predicted_future_value = int(p.market_value * growth_factor)

                    bid_rec = self.bidding_strategy.calculate_bid(
                        asking_price=p.price,
                        market_value=p.market_value,
                        value_score=value_score,
                        confidence=0.8,  # Conservative confidence for lineup trades
                        is_long_term_hold=True,  # LINEUP TRADES = long-term holds
                        average_points=p.average_points,  # Enable elite bidding for >70 pts
                        predicted_future_value=predicted_future_value,
                    )
                    smart_bids[p.id] = bid_rec.recommended_bid
                total_cost = sum(smart_bids.values())
            else:
                # Fallback to market value if no bidding strategy
                total_cost = sum(p.market_value for p in players_in)

            # Check budget constraint (need full cost, no proceeds from selling)
            if total_cost > current_budget:
                continue  # Cannot afford

            # Simulate new squad after buying players
            new_squad = list(current_squad)
            new_squad.extend(players_in)

            # Select new best 11 from new squad
            new_eleven = select_best_eleven(new_squad, player_values)
            new_points, new_value = self.calculate_lineup_strength(new_eleven, player_values)

            # Calculate improvements
            improvement_points = new_points - current_points
            improvement_value = new_value - current_value

            # Check if improvement meets threshold
            if (
                improvement_points < min_improvement_points
                and improvement_value < min_improvement_value
            ):
                continue  # Not worth it

            # Create recommendation with no players_out
            strategy = f"0-for-{len(players_in)}"

            trade = TradeRecommendation(
                players_out=[],  # No players sold
                players_in=list(players_in),
                improvement_points=improvement_points,
                improvement_value=improvement_value,
                total_cost=total_cost,
                total_proceeds=0,  # No proceeds
                net_cost=total_cost,  # Full cost (no offset from selling)
                required_budget=total_cost,
                strategy=strategy,
                smart_bids=smart_bids if smart_bids else None,
            )

            recommendations.append(trade)

    def filter_affordable_market(self, market_players: list, max_budget: int) -> list:
        """
        Filter market to only players we can afford

        Args:
            market_players: All market players
            max_budget: Maximum budget available

        Returns:
            List of affordable players
        """
        return [p for p in market_players if p.market_value <= max_budget]
