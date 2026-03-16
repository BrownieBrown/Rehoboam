"""DecisionEngine — EP-first buy/sell decision logic.

Uses select_best_eleven from rehoboam.formation to determine the optimal
starting 11 from a squad, then calculates marginal EP gain for candidates,
builds sell plans to fund purchases, and ranks squad players by expendability.
"""

from rehoboam.config import POSITION_MINIMUMS
from rehoboam.formation import select_best_eleven
from rehoboam.kickbase_client import MarketPlayer

from .models import (
    MarginalEPResult,
    PlayerScore,
    SellPlan,
    SellPlanEntry,
    SellRecommendation,
)


class DecisionEngine:
    """EP-based decision engine for buy/sell recommendations.

    Args:
        min_ep_to_buy:  Minimum expected points for a player to be worth buying.
        min_ep_upgrade: Minimum marginal EP gain required to recommend a buy.
    """

    def __init__(self, min_ep_to_buy: float = 30.0, min_ep_upgrade: float = 10.0) -> None:
        self.min_ep_to_buy = min_ep_to_buy
        self.min_ep_upgrade = min_ep_upgrade

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def select_lineup(self, squad_scores: list[PlayerScore]) -> dict[str, float]:
        """Return a ``{player_id: expected_points}`` mapping for all scored players.

        This dict is suitable as the ``player_values`` argument to
        :func:`~rehoboam.formation.select_best_eleven`.
        """
        return {s.player_id: s.expected_points for s in squad_scores}

    # ------------------------------------------------------------------
    # Core decision methods
    # ------------------------------------------------------------------

    def calculate_marginal_ep(
        self,
        candidate_score: PlayerScore,
        candidate_player: MarketPlayer,
        squad: list[MarketPlayer],
        squad_scores: list[PlayerScore],
    ) -> MarginalEPResult:
        """Calculate how much adding *candidate_player* improves the best-11 EP total.

        Steps:
        1. Build a score map for the current squad.
        2. Select current best-11 and sum their EP.
        3. Add the candidate to the squad (temporarily) and re-run best-11.
        4. ``marginal_ep_gain = max(0, new_total - current_total)``.
        5. Detect which player was displaced (in old best-11 but not in new).

        Returns a :class:`~rehoboam.scoring.models.MarginalEPResult`.
        """
        score_map = self.select_lineup(squad_scores)

        # Current best-11
        current_best = select_best_eleven(squad, score_map)
        current_total = sum(score_map.get(p.id, 0.0) for p in current_best)
        current_best_ids = {p.id for p in current_best}

        # Augmented squad with candidate
        augmented_squad = list(squad) + [candidate_player]
        augmented_score_map = dict(score_map)
        augmented_score_map[candidate_score.player_id] = candidate_score.expected_points

        new_best = select_best_eleven(augmented_squad, augmented_score_map)
        new_total = sum(augmented_score_map.get(p.id, 0.0) for p in new_best)
        new_best_ids = {p.id for p in new_best}

        marginal_gain = max(0.0, new_total - current_total)

        # Find who was displaced: was in old best-11 but not in new, and is not
        # the candidate itself.
        displaced_ids = current_best_ids - new_best_ids - {candidate_score.player_id}
        displaced_player_id: str | None = next(iter(displaced_ids), None)

        # Resolve displaced player name from squad
        displaced_player_name: str | None = None
        displaced_player_ep: float = 0.0
        if displaced_player_id:
            displaced_player = next((p for p in squad if p.id == displaced_player_id), None)
            if displaced_player:
                displaced_player_name = (
                    f"{displaced_player.first_name} {displaced_player.last_name}"
                )
            displaced_player_ep = score_map.get(displaced_player_id, 0.0)

        return MarginalEPResult(
            player_id=candidate_score.player_id,
            expected_points=candidate_score.expected_points,
            current_squad_ep=current_total,
            new_squad_ep=new_total,
            marginal_ep_gain=marginal_gain,
            replaces_player_id=displaced_player_id if marginal_gain > 0 else None,
            replaces_player_name=displaced_player_name if marginal_gain > 0 else None,
            replaces_player_ep=displaced_player_ep,
        )

    def build_sell_plan(
        self,
        bid_amount: int,
        current_budget: int,
        squad: list[MarketPlayer],
        squad_scores: list[PlayerScore],
        best_11_ids: set[str],
        displaced_player_id: str | None,
    ) -> SellPlan:
        """Build a sell plan that covers the budget shortfall for *bid_amount*.

        Rules:
        - If ``bid_amount <= current_budget``: viable, no sells needed.
        - Otherwise greedily sell bench players (not in best-11) sorted by
          lowest EP first, then add the displaced player if still short.
        - Non-displaced best-11 starters are protected and cannot be sold.
        - ``expected_sell_value = market_value * 0.95``.

        Returns a :class:`~rehoboam.scoring.models.SellPlan`.
        """
        shortfall = bid_amount - current_budget

        if shortfall <= 0:
            return SellPlan(
                players_to_sell=[],
                total_recovery=0,
                net_budget_after=current_budget - bid_amount,
                is_viable=True,
                ep_impact=0.0,
                reasoning="Bid within current budget — no sells required.",
            )

        score_map = {s.player_id: s for s in squad_scores}

        # Determine position counts to enforce minimums
        position_counts: dict[str, int] = {}
        for p in squad:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1

        # Identify sell candidates
        # Priority: displaced player first, then bench players sorted by lowest EP
        # Protected: non-displaced best-11 starters + position-minimum holders

        def _is_at_minimum(player: MarketPlayer) -> bool:
            """True if selling this player would drop position below the minimum."""
            minimum = POSITION_MINIMUMS.get(player.position, 0)
            current = position_counts.get(player.position, 0)
            return current <= minimum

        def _ep(player: MarketPlayer) -> float:
            ps = score_map.get(player.id)
            return ps.expected_points if ps else 0.0

        # Categorise squad members into three tiers:
        #   1. displaced player — first candidate (no longer in new best-11)
        #   2. bench players (not in best-11, excluding displaced)
        #   3. non-displaced starters above their position minimum (last resort)
        # Players at or below position minimum are never sold.
        displaced_player: MarketPlayer | None = None
        bench_candidates: list[MarketPlayer] = []
        starter_surplus: list[MarketPlayer] = []

        for player in squad:
            if player.id == displaced_player_id:
                displaced_player = player
                continue
            if player.id in best_11_ids:
                # Starter (not displaced) — last-resort sell candidate
                starter_surplus.append(player)
            else:
                # Bench player
                bench_candidates.append(player)

        # Sort by ascending EP within each tier (most expendable first)
        bench_candidates.sort(key=_ep)
        starter_surplus.sort(key=_ep)

        # Build ordered list: displaced → bench → starter surplus
        ordered_candidates: list[MarketPlayer] = []
        if displaced_player is not None:
            ordered_candidates.append(displaced_player)
        ordered_candidates.extend(bench_candidates)
        ordered_candidates.extend(starter_surplus)

        players_to_sell: list[SellPlanEntry] = []
        total_recovery = 0
        ep_impact = 0.0
        # Track how many of each position we'd still have after sells
        remaining_counts = dict(position_counts)

        for player in ordered_candidates:
            if total_recovery >= shortfall:
                break

            # Check position minimum with remaining counts
            minimum = POSITION_MINIMUMS.get(player.position, 0)
            if remaining_counts.get(player.position, 0) <= minimum:
                continue  # selling would breach minimum — skip

            ps = score_map.get(player.id)
            player_ep = ps.expected_points if ps else 0.0
            sell_value = int(player.market_value * 0.95)

            players_to_sell.append(
                SellPlanEntry(
                    player_id=player.id,
                    player_name=f"{player.first_name} {player.last_name}",
                    expected_sell_value=sell_value,
                    player_ep=player_ep,
                    is_in_best_11=player.id in best_11_ids,
                )
            )
            total_recovery += sell_value
            ep_impact += player_ep
            remaining_counts[player.position] = remaining_counts.get(player.position, 0) - 1

        net_budget_after = current_budget + total_recovery - bid_amount
        is_viable = net_budget_after >= 0

        reasoning = (
            f"Sell {len(players_to_sell)} player(s) to recover "
            f"€{total_recovery:,} and cover €{shortfall:,} shortfall."
            if players_to_sell
            else "Cannot cover shortfall — all candidates are protected."
        )

        return SellPlan(
            players_to_sell=players_to_sell,
            total_recovery=total_recovery,
            net_budget_after=net_budget_after,
            is_viable=is_viable,
            ep_impact=ep_impact,
            reasoning=reasoning,
        )

    def recommend_sells(
        self,
        squad: list[MarketPlayer],
        squad_scores: list[PlayerScore],
        best_11_ids: set[str],
    ) -> list[SellRecommendation]:
        """Rank all squad players by expendability (highest = most expendable).

        Formula::

            expendability = 100 - expected_points + (20 if not in best-11 else 0)

        Players at or below their position minimum are marked as protected.

        Returns a sorted list of :class:`~rehoboam.scoring.models.SellRecommendation`
        (most expendable first).
        """
        score_map = {s.player_id: s for s in squad_scores}

        # Count positions to detect position minimums
        position_counts: dict[str, int] = {}
        for p in squad:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1

        recommendations: list[SellRecommendation] = []
        for player in squad:
            ps = score_map.get(player.id)
            ep = ps.expected_points if ps else 0.0
            in_best_11 = player.id in best_11_ids

            bench_bonus = 0.0 if in_best_11 else 20.0
            expendability = 100.0 - ep + bench_bonus

            minimum = POSITION_MINIMUMS.get(player.position, 0)
            is_protected = position_counts.get(player.position, 0) <= minimum
            protection_reason = (
                f"Only {position_counts.get(player.position, 0)} "
                f"{player.position}(s) — minimum is {minimum}"
                if is_protected
                else None
            )

            reason_parts = []
            if not in_best_11:
                reason_parts.append("bench player")
            reason_parts.append(f"EP={ep:.1f}")
            if is_protected:
                reason_parts.append("position minimum")
            reason = "; ".join(reason_parts)

            recommendations.append(
                SellRecommendation(
                    score=ps if ps is not None else _dummy_score(player.id, ep),
                    expendability=expendability,
                    is_protected=is_protected,
                    protection_reason=protection_reason,
                    reason=reason,
                )
            )

        recommendations.sort(key=lambda r: r.expendability, reverse=True)
        return recommendations


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dummy_score(player_id: str, ep: float) -> PlayerScore:
    """Minimal PlayerScore for players missing from score_map."""
    from rehoboam.scoring.models import DataQuality

    dq = DataQuality(
        grade="F",
        games_played=0,
        consistency=0.0,
        has_fixture_data=False,
        has_lineup_data=False,
        warnings=["No score data available"],
    )
    return PlayerScore(
        player_id=player_id,
        expected_points=ep,
        data_quality=dq,
        base_points=0.0,
        consistency_bonus=0.0,
        lineup_bonus=0.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=0,
        market_value=0,
    )
