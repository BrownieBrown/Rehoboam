"""Roster analysis for context-aware buy recommendations"""

from .analyzer import RosterContext, RosterImpact
from .config import MIN_UPGRADE_THRESHOLD, POSITION_MINIMUMS
from .value_calculator import PlayerValue


class RosterAnalyzer:
    """Analyzes roster composition for context-aware recommendations"""

    def __init__(
        self, position_minimums: dict | None = None, upgrade_threshold: float | None = None
    ):
        """
        Initialize RosterAnalyzer.

        Args:
            position_minimums: Override default position minimums
            upgrade_threshold: Minimum value score improvement to be considered an upgrade
        """
        self.position_minimums = position_minimums or POSITION_MINIMUMS
        self.upgrade_threshold = upgrade_threshold or MIN_UPGRADE_THRESHOLD

    def analyze_roster(
        self,
        squad: list,
        player_stats: dict[str, dict],
        player_values: dict[str, float] | None = None,
    ) -> dict[str, RosterContext]:
        """
        Analyze current roster composition by position.

        Args:
            squad: List of Player objects in the squad
            player_stats: Dict mapping player_id -> stats from API (includes trp = purchase price)
            player_values: Optional dict mapping player_id -> pre-calculated value score

        Returns:
            Dict mapping position -> RosterContext
        """
        roster_contexts = {}

        # Group players by position
        players_by_position: dict[str, list] = {}
        for player in squad:
            position = player.position
            if position not in players_by_position:
                players_by_position[position] = []
            players_by_position[position].append(player)

        # Analyze each position
        for position, minimum_count in self.position_minimums.items():
            players = players_by_position.get(position, [])

            # Calculate value scores and get purchase prices for each player
            existing_players = []
            for player in players:
                # Get stats for this player
                stats = player_stats.get(player.id, {})

                # Extract purchase price (trp = transfer price paid)
                purchase_price = player.market_value  # Default to current value
                if stats and isinstance(stats, dict):
                    purchase_price = stats.get("trp", player.market_value)

                # Use pre-calculated value score if available, otherwise calculate
                value_score = 0.0
                if player_values and player.id in player_values:
                    value_score = player_values[player.id]
                else:
                    try:
                        player_value = PlayerValue.calculate(player)
                        value_score = player_value.value_score
                    except Exception:
                        value_score = 0.0

                existing_players.append(
                    {
                        "player": player,
                        "name": f"{player.first_name} {player.last_name}".strip()
                        or player.last_name,
                        "purchase_price": purchase_price,
                        "current_value": player.market_value,
                        "value_score": value_score,
                    }
                )

            # Sort by value score (lowest first for finding weakest)
            existing_players.sort(key=lambda x: x["value_score"])

            # Find weakest player (if any exist)
            weakest_player = existing_players[0] if existing_players else None

            # Determine if position is below minimum
            current_count = len(players)
            is_below_minimum = current_count < minimum_count

            roster_contexts[position] = RosterContext(
                position=position,
                current_count=current_count,
                minimum_count=minimum_count,
                existing_players=existing_players,
                weakest_player=weakest_player,
                is_below_minimum=is_below_minimum,
                upgrade_potential=0.0,  # Will be set per-player when analyzing market
            )

        return roster_contexts

    def get_roster_impact(
        self,
        market_player,
        market_player_score: float,
        roster_context: RosterContext | None,
    ) -> RosterImpact:
        """
        Determine the roster impact of buying a market player.

        Args:
            market_player: MarketPlayer being evaluated
            market_player_score: The value score of the market player
            roster_context: Context for this position

        Returns:
            RosterImpact with upgrade info
        """
        # No roster context - treat as filling a gap
        if not roster_context:
            return RosterImpact(
                is_upgrade=False,
                impact_type="fills_gap",
                replaces_player=None,
                value_score_gain=0.0,
                net_cost=market_player.price,
                reason="New position player",
            )

        # Position is BELOW minimum - this fills a needed gap
        if roster_context.is_below_minimum:
            return RosterImpact(
                is_upgrade=False,
                impact_type="fills_gap",
                replaces_player=None,
                value_score_gain=0.0,
                net_cost=market_player.price,
                reason=f"Fills gap ({roster_context.current_count}/{roster_context.minimum_count} {roster_context.position}s)",
            )

        # Position is at/above minimum - check if this is an upgrade
        weakest = roster_context.weakest_player
        if not weakest:
            # No existing players to compare against
            return RosterImpact(
                is_upgrade=False,
                impact_type="additional",
                replaces_player=None,
                value_score_gain=0.0,
                net_cost=market_player.price,
                reason=f"Additional {roster_context.position}",
            )

        # Calculate value score gain over weakest player
        value_score_gain = market_player_score - weakest["value_score"]

        # Calculate net cost (buy price - sell value of replaced player)
        net_cost = market_player.price - weakest["current_value"]

        # Is this a significant upgrade?
        is_upgrade = value_score_gain >= self.upgrade_threshold

        if is_upgrade:
            return RosterImpact(
                is_upgrade=True,
                impact_type="upgrade",
                replaces_player=weakest["name"],
                value_score_gain=value_score_gain,
                net_cost=net_cost,
                reason=f"Replaces {weakest['name']} (+{value_score_gain:.0f} value)",
            )
        else:
            # Show value score gain (negative means worse than current player)
            gain_str = (
                f"+{value_score_gain:.0f}" if value_score_gain >= 0 else f"{value_score_gain:.0f}"
            )
            return RosterImpact(
                is_upgrade=False,
                impact_type="not_upgrade",
                replaces_player=weakest["name"],
                value_score_gain=value_score_gain,
                net_cost=market_player.price,
                reason=f"vs {weakest['name']} ({gain_str})",
            )

    def calculate_upgrade_potential(
        self, market_player_score: float, roster_context: RosterContext
    ) -> float:
        """
        Calculate how much better a market player is vs the weakest existing player.

        Args:
            market_player_score: Value score of market player
            roster_context: Context for this position

        Returns:
            Upgrade potential (positive = better, negative = worse)
        """
        if not roster_context.weakest_player:
            return 0.0

        return market_player_score - roster_context.weakest_player["value_score"]
