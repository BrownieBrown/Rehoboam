"""Evaluates whether a player replacement/upgrade makes financial and tactical sense"""

from dataclasses import dataclass

from .analyzer import PlayerAnalysis


@dataclass
class ReplacementEvaluation:
    """Evaluation of whether to replace one player with another"""

    can_replace: bool
    reason: str
    net_cost: int  # What we pay minus what we get from sale
    value_improvement: float  # Difference in value scores
    price_ratio: float  # New price / old price


class ReplacementEvaluator:
    """Evaluates player replacements for lineup upgrades"""

    # Minimum position requirements for valid lineup
    MIN_POSITIONS = {
        "Goalkeeper": 1,
        "Defender": 3,
        "Midfielder": 2,  # Typical minimum
        "Forward": 1,
    }

    def __init__(
        self,
        max_price_ratio: float = 2.0,  # Replacement can cost max 2x the player being sold
        min_value_score_improvement: float = 15.0,  # Must be significantly better
        max_net_cost_of_team_value: float = 0.10,  # Max 10% of team value for upgrade
    ):
        self.max_price_ratio = max_price_ratio
        self.min_value_score_improvement = min_value_score_improvement
        self.max_net_cost_of_team_value = max_net_cost_of_team_value

    def _count_positions(self, squad: list[PlayerAnalysis]) -> dict[str, int]:
        """Count how many players we have in each position"""
        counts = {
            "Goalkeeper": 0,
            "Defender": 0,
            "Midfielder": 0,
            "Forward": 0,
        }

        for player_analysis in squad:
            position = player_analysis.player.position
            if position in counts:
                counts[position] += 1

        return counts

    def _would_violate_position_requirements(
        self, squad: list[PlayerAnalysis], player_to_remove: PlayerAnalysis
    ) -> bool:
        """
        Check if removing this player would violate minimum position requirements

        Args:
            squad: Current squad
            player_to_remove: Player we want to sell

        Returns:
            True if removing would violate requirements, False otherwise
        """
        position_counts = self._count_positions(squad)
        position_to_remove = player_to_remove.player.position

        # Simulate removal
        new_count = position_counts[position_to_remove] - 1

        # Check if we'd drop below minimum
        if new_count < self.MIN_POSITIONS[position_to_remove]:
            return True

        return False

    def evaluate_replacement(
        self,
        current_player: PlayerAnalysis,
        replacement: PlayerAnalysis,
        current_squad: list[PlayerAnalysis],
        team_value: int,
        available_budget: int,
    ) -> ReplacementEvaluation:
        """
        Evaluate if replacing current player with replacement makes sense

        Args:
            current_player: Analysis of player currently in lineup
            replacement: Analysis of potential replacement from market
            current_squad: Current squad to check position requirements
            team_value: Total team value
            available_budget: Available budget for trading

        Returns:
            ReplacementEvaluation with decision and reasoning
        """
        # Check position requirements - can we afford to lose this position?
        if self._would_violate_position_requirements(current_squad, current_player):
            return ReplacementEvaluation(
                can_replace=False,
                reason=f"Cannot remove {current_player.player.position}: would violate minimum position requirements",
                net_cost=0,
                value_improvement=0.0,
                price_ratio=0.0,
            )

        # Calculate financial metrics
        sell_price = current_player.market_value  # What we get from selling
        buy_price = replacement.current_price  # What we pay for replacement
        net_cost = buy_price - sell_price
        price_ratio = buy_price / max(sell_price, 1)

        # Calculate value improvement
        value_improvement = replacement.value_score - current_player.value_score

        # Check if we can afford it
        if net_cost > available_budget:
            return ReplacementEvaluation(
                can_replace=False,
                reason=f"Cannot afford: net cost €{net_cost:,} exceeds budget €{available_budget:,}",
                net_cost=net_cost,
                value_improvement=value_improvement,
                price_ratio=price_ratio,
            )

        # Check price ratio - don't swap €5M for €60M player
        if price_ratio > self.max_price_ratio:
            return ReplacementEvaluation(
                can_replace=False,
                reason=f"Price too different: {price_ratio:.1f}x current player (max: {self.max_price_ratio}x)",
                net_cost=net_cost,
                value_improvement=value_improvement,
                price_ratio=price_ratio,
            )

        # Check value improvement - must be significantly better
        if value_improvement < self.min_value_score_improvement:
            return ReplacementEvaluation(
                can_replace=False,
                reason=f"Not enough improvement: +{value_improvement:.1f} value score (min: {self.min_value_score_improvement})",
                net_cost=net_cost,
                value_improvement=value_improvement,
                price_ratio=price_ratio,
            )

        # Check net cost as % of team value
        max_net_cost = int(team_value * self.max_net_cost_of_team_value)
        if net_cost > max_net_cost:
            return ReplacementEvaluation(
                can_replace=False,
                reason=f"Net cost €{net_cost:,} exceeds {self.max_net_cost_of_team_value*100}% of team value",
                net_cost=net_cost,
                value_improvement=value_improvement,
                price_ratio=price_ratio,
            )

        # All checks passed - this is a good upgrade
        current_pos = current_player.player.position
        replacement_pos = replacement.player.position
        position_info = (
            f" ({current_pos} → {replacement_pos})" if current_pos != replacement_pos else ""
        )

        return ReplacementEvaluation(
            can_replace=True,
            reason=f"Good upgrade{position_info}: +{value_improvement:.1f} value score for €{net_cost:,} net cost",
            net_cost=net_cost,
            value_improvement=value_improvement,
            price_ratio=price_ratio,
        )

    def find_best_replacements(
        self,
        current_squad: list[PlayerAnalysis],
        market_opportunities: list[PlayerAnalysis],
        team_value: int,
        available_budget: int,
        starting_eleven_ids: set[str],
    ) -> list[tuple[PlayerAnalysis, PlayerAnalysis, ReplacementEvaluation]]:
        """
        Find best replacement opportunities

        Returns:
            List of (current_player, replacement, evaluation) tuples for valid upgrades
        """
        replacements = []

        for current in current_squad:
            # Only consider players in starting 11 for replacement
            if current.player.id not in starting_eleven_ids:
                continue

            # Find potential replacements from market
            for replacement in market_opportunities:
                evaluation = self.evaluate_replacement(
                    current_player=current,
                    replacement=replacement,
                    current_squad=current_squad,
                    team_value=team_value,
                    available_budget=available_budget,
                )

                if evaluation.can_replace:
                    replacements.append((current, replacement, evaluation))

        # Sort by value improvement / net cost ratio (best value for money)
        # Handle case where net_cost might be negative (we profit from the swap!)
        replacements.sort(
            key=lambda x: x[2].value_improvement / max(abs(x[2].net_cost), 1000), reverse=True
        )

        return replacements
