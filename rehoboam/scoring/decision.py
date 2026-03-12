"""DecisionEngine — buy/sell/lineup/trade-pair decisions from PlayerScore."""

from ..analyzer import RosterContext
from ..kickbase_client import MarketPlayer
from .models import BuyRecommendation, PlayerScore, SellRecommendation, TradePair


class DecisionEngine:
    """Makes buy/sell/lineup decisions from PlayerScore lists."""

    def __init__(
        self,
        min_avg_points_to_buy: float = 20.0,
        min_avg_points_emergency: float = 10.0,
        min_expected_points_to_buy: float = 30.0,
        min_ep_upgrade_threshold: float = 10.0,
    ):
        self.min_avg_points_to_buy = min_avg_points_to_buy
        self.min_avg_points_emergency = min_avg_points_emergency
        self.min_expected_points_to_buy = min_expected_points_to_buy
        self.min_ep_upgrade_threshold = min_ep_upgrade_threshold

    def recommend_buys(
        self,
        market_scores: list[PlayerScore],
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
        budget: int,
        market_players: dict[str, MarketPlayer],
        is_emergency: bool = False,
        top_n: int = 5,
    ) -> list[BuyRecommendation]:
        """Recommend players to buy, sorted by effective EP."""
        min_avg = self.min_avg_points_emergency if is_emergency else self.min_avg_points_to_buy
        recs = []

        for score in market_scores:
            # Quality gates
            if score.data_quality.grade == "F":
                continue
            if score.average_points < min_avg:
                continue
            if score.expected_points < self.min_expected_points_to_buy:
                continue
            if score.current_price > budget:
                continue
            if score.status != 0:
                continue

            player = market_players.get(score.player_id)
            if not player:
                continue

            # Roster bonus
            roster_bonus = 0.0
            reason = "additional"
            ctx = roster_context.get(score.position)
            if ctx and ctx.is_below_minimum:
                roster_bonus = 10.0
                reason = "fills_gap"
            elif ctx and not ctx.is_below_minimum:
                reason = "upgrade"

            recs.append(
                BuyRecommendation(
                    player=player,
                    score=score,
                    roster_bonus=roster_bonus,
                    reason=reason,
                )
            )

        recs.sort(key=lambda r: r.effective_ep, reverse=True)
        return recs[:top_n]

    def recommend_sells(
        self,
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
        squad_players: dict[str, MarketPlayer],
    ) -> list[SellRecommendation]:
        """Recommend players to sell, sorted by lowest EP first."""
        recs = []

        for score in squad_scores:
            player = squad_players.get(score.player_id)
            if not player:
                continue

            # Check position protection — try score.position first, then player.position
            is_protected = False
            protection_reason = None
            ctx = roster_context.get(score.position) or roster_context.get(player.position)
            if ctx and ctx.current_count <= ctx.minimum_count:
                is_protected = True
                protection_reason = f"Min {score.position}"

            recs.append(
                SellRecommendation(
                    player=player,
                    score=score,
                    is_protected=is_protected,
                    protection_reason=protection_reason,
                    budget_recovery=score.market_value,
                )
            )

        recs.sort(key=lambda r: r.score.expected_points)
        return recs

    def build_trade_pairs(
        self,
        market_scores: list[PlayerScore],
        squad_scores: list[PlayerScore],
        roster_context: dict[str, RosterContext],
        budget: int,
        market_players: dict[str, MarketPlayer],
        squad_players: dict[str, MarketPlayer],
        top_n: int = 5,
    ) -> list[TradePair]:
        """Build sell->buy swap pairs with positive EP gain."""
        # Get unprotected sell candidates sorted by lowest EP
        sells = self.recommend_sells(squad_scores, roster_context, squad_players)
        sellable = [s for s in sells if not s.is_protected]

        # Get buy candidates (relaxed budget — selling frees money)
        buys = self.recommend_buys(
            market_scores,
            squad_scores,
            roster_context,
            budget=budget + max((s.budget_recovery for s in sellable), default=0),
            market_players=market_players,
            top_n=20,
        )

        pairs = []
        used_sells = set()
        used_buys = set()

        for buy_rec in buys:
            for sell_rec in sellable:
                if sell_rec.score.player_id in used_sells:
                    continue
                if buy_rec.score.player_id in used_buys:
                    break

                ep_gain = buy_rec.score.expected_points - sell_rec.score.expected_points
                net_cost = buy_rec.score.current_price - sell_rec.score.market_value

                if ep_gain >= self.min_ep_upgrade_threshold and net_cost <= budget:
                    pairs.append(
                        TradePair(
                            buy_player=buy_rec.player,
                            sell_player=sell_rec.player,
                            buy_score=buy_rec.score,
                            sell_score=sell_rec.score,
                        )
                    )
                    used_sells.add(sell_rec.score.player_id)
                    used_buys.add(buy_rec.score.player_id)
                    break

        return pairs[:top_n]

    def select_lineup(
        self,
        squad_scores: list[PlayerScore],
    ) -> dict[str, float]:
        """Return {player_id: expected_points} for formation.select_best_eleven()."""
        return {s.player_id: s.expected_points for s in squad_scores}
