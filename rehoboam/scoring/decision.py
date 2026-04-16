"""DecisionEngine — EP-first buy/sell decision logic.

Uses select_best_eleven from rehoboam.formation to determine the optimal
starting 11 from a squad, then calculates marginal EP gain for candidates,
builds sell plans to fund purchases, and ranks squad players by expendability.
"""

from rehoboam.config import MAX_LINEUP_PROB_FOR_BUY, POSITION_MINIMUMS
from rehoboam.formation import _POSITION_MAX_STARTERS, select_best_eleven
from rehoboam.kickbase_client import MarketPlayer

from .models import (
    BuyRecommendation,
    MarginalEPResult,
    PlayerScore,
    SellPlan,
    SellPlanEntry,
    SellRecommendation,
    TradePair,
)


class DecisionEngine:
    """EP-based decision engine for buy/sell recommendations.

    Args:
        min_ep_to_buy:  Minimum expected points for a player to be worth buying.
        min_ep_upgrade: Minimum marginal EP gain required to recommend a buy.
    """

    def __init__(self, min_ep_to_buy: float = 30.0, min_ep_upgrade: float = 5.0) -> None:
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
        incoming_position: str | None = None,
    ) -> SellPlan:
        """Build a sell plan that covers the budget shortfall for *bid_amount*.

        Rules:
        - If ``bid_amount <= current_budget``: viable, no sells needed.
        - Otherwise greedily sell bench players (not in best-11) sorted by
          lowest EP first, then add the displaced player if still short.
        - Non-displaced best-11 starters are protected and cannot be sold.
        - ``expected_sell_value = market_value * 0.95``.

        *incoming_position* (optional): the position of the player being
        bought.  When set, the position count for that position is
        incremented by 1 before checking minimums.  This allows selling a
        same-position player that would otherwise be at the minimum (e.g.
        selling the old GK when buying a new GK).

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

        # Determine position counts to enforce minimums.
        # When incoming_position is set, account for the buy that will add
        # one player at that position, making a same-position sell safe.
        position_counts: dict[str, int] = {}
        for p in squad:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1
        if incoming_position:
            position_counts[incoming_position] = position_counts.get(incoming_position, 0) + 1

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

    def recommend_buys(
        self,
        market_scores: list[PlayerScore],
        squad_scores: list[PlayerScore],
        roster_context: dict,
        budget: float,
        market_players: dict[str, MarketPlayer],
        is_emergency: bool = False,
        top_n: int = 5,
        squad_players: dict[str, MarketPlayer] | None = None,
    ) -> list[BuyRecommendation]:
        """Rank market players by marginal EP gain to the squad.

        For each market player above the EP threshold, calculates how much
        they would improve the best-11 total EP if added to the squad.
        Players over budget get a paired sell plan instead of being filtered out.

        Returns the top *top_n* recommendations sorted by marginal EP gain.
        """
        lineup_map = self.select_lineup(squad_scores)

        # Compute best-11 IDs for sell plan generation
        squad_list = list(squad_players.values()) if squad_players else []
        if squad_list:
            best_11 = select_best_eleven(squad_list, lineup_map)
            best_11_ids = {p.id for p in best_11}
        else:
            best_11_ids = set()

        recs: list[BuyRecommendation] = []
        for ps in market_scores:
            player = market_players.get(ps.player_id)
            if not player:
                continue

            # Filter by minimum EP threshold
            min_ep = 10.0 if is_emergency else self.min_ep_to_buy
            if ps.expected_points < min_ep:
                continue

            # Skip players unlikely to start (prob 4-5)
            if (
                ps.lineup_probability is not None
                and ps.lineup_probability > MAX_LINEUP_PROB_FOR_BUY
            ):
                continue

            # Hard-block players with severely declining minutes
            if ps.minutes_trend == "decreasing" and ps.minutes_bonus <= -15.0:
                continue

            # Calculate marginal EP gain using formation-aware logic
            if squad_list:
                mep = self.calculate_marginal_ep(
                    candidate_score=ps,
                    candidate_player=player,
                    squad=squad_list,
                    squad_scores=squad_scores,
                )
                marginal = mep.marginal_ep_gain
                replaces_id = mep.replaces_player_id
                replaces_name = mep.replaces_player_name
            else:
                # No squad data — treat as pure EP gain
                marginal = ps.expected_points
                replaces_id = None
                replaces_name = None

            # Dead-weight guard: if buying this player would saturate their
            # position (e.g. 2nd GK when max fieldable is 1), force a sell
            # plan so the weakest same-position peer gets sold after auction.
            force_sell_displaced = bool(squad_list) and _would_create_dead_weight(
                player, squad_list
            )

            # Determine roster impact — count by actual squad composition,
            # not by scored players, so positions without scores still count.
            pos_counts: dict[str, int] = {}
            for p in squad_list:
                pos_counts[p.position] = pos_counts.get(p.position, 0) + 1

            pos_min = POSITION_MINIMUMS.get(player.position, 0)
            current_count = pos_counts.get(player.position, 0)

            if current_count < pos_min:
                roster_impact = "fills_gap"
                roster_bonus = 10.0
            elif marginal > self.min_ep_upgrade:
                roster_impact = "upgrade"
                roster_bonus = 5.0
            else:
                roster_impact = "additional"
                roster_bonus = 0.0

            # Build reason string
            reason_parts = [f"EP {ps.expected_points:.1f}"]
            if marginal > 0:
                reason_parts.append(f"+{marginal:.1f} marginal")
            if roster_impact == "fills_gap":
                reason_parts.append("fills gap")
            elif replaces_id:
                reason_parts.append("upgrades pos")
            if force_sell_displaced:
                reason_parts.append("sell old after auction")

            # Only recommend if marginal gain meets threshold (or emergency)
            if not is_emergency and marginal < self.min_ep_upgrade and roster_impact != "fills_gap":
                continue

            # Build forced sell plan for dead-weight buys upfront so it's
            # attached to the recommendation before the budget filter below.
            # The sell target is the weakest squad player at the candidate's
            # position — NOT necessarily the player displaced from the best-11
            # (which may be a different position entirely).
            #
            # Unlike budget sell plans, this isn't about funding the purchase —
            # it's about removing the player who becomes permanently unfieldable.
            # The sell is deferred until after the auction resolves.
            forced_sell_plan: SellPlan | None = None
            if force_sell_displaced:
                score_lookup = {s.player_id: s.expected_points for s in squad_scores}
                pos_peers = [p for p in squad_list if p.position == player.position]
                pos_peers.sort(key=lambda p: score_lookup.get(p.id, 0.0))
                dead_weight = pos_peers[0] if pos_peers else None

                if dead_weight is None:
                    continue

                # Safety: don't sell the dead-weight player if it would drop the
                # position below the formation minimum.  The incoming buy adds 1,
                # so the post-trade count is ``len(pos_peers) + 1 - 1 = len(pos_peers)``.
                # If that's still at or above the minimum, the sell is safe.
                pos_min = POSITION_MINIMUMS.get(player.position, 0)
                if len(pos_peers) < pos_min:
                    # Selling would breach the minimum — skip this buy entirely.
                    continue

                dw_ep = score_lookup.get(dead_weight.id, 0.0)
                dw_sell_value = int(dead_weight.market_value * 0.95)
                net_after = int(budget) - player.price + dw_sell_value

                forced_sell_plan = SellPlan(
                    players_to_sell=[
                        SellPlanEntry(
                            player_id=dead_weight.id,
                            player_name=(f"{dead_weight.first_name} {dead_weight.last_name}"),
                            expected_sell_value=dw_sell_value,
                            player_ep=dw_ep,
                            is_in_best_11=dead_weight.id in best_11_ids,
                        )
                    ],
                    total_recovery=dw_sell_value,
                    net_budget_after=net_after,
                    is_viable=net_after >= 0,
                    ep_impact=dw_ep,
                    reasoning=(
                        f"Sell {dead_weight.first_name} {dead_weight.last_name} "
                        f"(dead weight {player.position}) after winning auction."
                    ),
                )
                if not forced_sell_plan.is_viable:
                    continue

            recs.append(
                BuyRecommendation(
                    score=ps,
                    player=player,
                    marginal_ep_gain=marginal,
                    effective_ep=ps.expected_points,
                    replaces_player_id=replaces_id,
                    replaces_player_name=replaces_name,
                    roster_impact=roster_impact,
                    roster_bonus=roster_bonus,
                    reason="; ".join(reason_parts),
                    sell_plan=forced_sell_plan,
                )
            )

        # For over-budget buys, generate sell plans instead of filtering.
        # Buys that already have a forced sell plan (dead-weight guard) keep
        # it — the budget shortfall is already covered by the forced plan.
        final_recs: list[BuyRecommendation] = []
        for rec in recs:
            if rec.sell_plan is not None:
                # Already has a sell plan (forced dead-weight or pre-attached)
                final_recs.append(rec)
            elif rec.player.price <= budget:
                final_recs.append(rec)
            elif squad_list:
                # Player costs more than budget — generate sell plan
                sell_plan = self.build_sell_plan(
                    bid_amount=rec.player.price,
                    current_budget=int(budget),
                    squad=squad_list,
                    squad_scores=squad_scores,
                    best_11_ids=best_11_ids,
                    displaced_player_id=rec.replaces_player_id,
                    incoming_position=rec.player.position,
                )
                if sell_plan.is_viable:
                    rec.sell_plan = sell_plan
                    final_recs.append(rec)
            # else: skip — no squad data to generate sell plan

        # Sort by marginal EP gain (primary), then by raw EP (secondary)
        final_recs.sort(key=lambda r: (r.marginal_ep_gain, r.score.expected_points), reverse=True)
        return final_recs[:top_n]

    def build_trade_pairs(
        self,
        market_scores: list[PlayerScore],
        squad_scores: list[PlayerScore],
        roster_context: dict,
        budget: float,
        market_players: dict[str, MarketPlayer],
        squad_players: dict[str, MarketPlayer],
        top_n: int = 5,
    ) -> list[TradePair]:
        """Build sell->buy trade pairs for a full squad (15/15).

        For each promising market player, finds the best squad member to sell
        and calculates net cost and EP gain. Always prefers bench players as
        sell targets — starters are only sacrificed for significant upgrades
        (2x the normal EP threshold).
        """
        # Get sell candidates sorted by expendability
        sell_recs = self.recommend_sells(
            squad_scores=squad_scores,
            roster_context=roster_context,
            squad_players=squad_players,
        )
        sellable = [r for r in sell_recs if not r.is_protected]

        # Compute formation-aware best-11 so we can split bench from starters.
        # Bench players are the preferred sell targets — selling starters
        # destroys team value and usually makes no sense.
        score_map = {s.player_id: s.expected_points for s in squad_scores}
        squad_list = list(squad_players.values())
        best_11 = select_best_eleven(squad_list, score_map)
        best_11_ids = {p.id for p in best_11}

        bench_sellable = [r for r in sellable if r.score.player_id not in best_11_ids]
        starter_sellable = [r for r in sellable if r.score.player_id in best_11_ids]

        pairs: list[TradePair] = []
        used_sell_ids: set[str] = set()

        # Score and sort market candidates by EP
        candidates = sorted(market_scores, key=lambda s: s.expected_points, reverse=True)

        for ps in candidates:
            buy_player = market_players.get(ps.player_id)
            if not buy_player:
                continue
            if ps.expected_points < self.min_ep_to_buy:
                continue

            # Skip players unlikely to start or with collapsing minutes
            if (
                ps.lineup_probability is not None
                and ps.lineup_probability > MAX_LINEUP_PROB_FOR_BUY
            ):
                continue
            if ps.minutes_trend == "decreasing" and ps.minutes_bonus <= -15.0:
                continue

            # Find best sell target — bench first, then starter as last resort.
            # Selection order:
            #   1. Same-position bench player (natural upgrade)
            #   2. Any bench player (free slot for position shift)
            #   3. Same-position starter (requires significant upgrade)
            def _pick(
                pool: list[SellRecommendation],
                want_pos: str | None,
            ) -> SellRecommendation | None:
                for sr in pool:
                    if sr.score.player_id in used_sell_ids:
                        continue
                    if sr.player is None:
                        continue
                    if want_pos is not None and sr.player.position != want_pos:
                        continue
                    return sr
                return None

            best_sell = _pick(bench_sellable, buy_player.position)
            if best_sell is None:
                best_sell = _pick(bench_sellable, None)
            is_starter_sell = False
            if best_sell is None:
                # Last resort: swap a same-position starter for a significant upgrade.
                best_sell = _pick(starter_sellable, buy_player.position)
                is_starter_sell = best_sell is not None

            if best_sell is None or best_sell.player is None:
                continue

            sell_mp = best_sell.player
            sell_value = int(sell_mp.market_value * 0.95)
            net_cost = buy_player.price - sell_value
            ep_gain = ps.expected_points - best_sell.score.expected_points

            # Starter swaps churn team value — require a significant EP boost
            # (2x the normal threshold) to justify the cost.
            min_gain = self.min_ep_upgrade * 2 if is_starter_sell else self.min_ep_upgrade
            if ep_gain < min_gain:
                continue

            # Check affordability (budget + sell recovery)
            if net_cost > budget:
                continue

            used_sell_ids.add(best_sell.score.player_id)

            pairs.append(
                TradePair(
                    buy_player=buy_player,
                    sell_player=sell_mp,
                    buy_score=ps,
                    sell_score=best_sell.score,
                    net_cost=net_cost,
                    ep_gain=ep_gain,
                )
            )

            if len(pairs) >= top_n:
                break

        pairs.sort(key=lambda p: p.ep_gain, reverse=True)
        return pairs[:top_n]

    def recommend_sells(
        self,
        squad_scores: list[PlayerScore],
        roster_context: dict,
        squad_players: dict[str, MarketPlayer],
    ) -> list[SellRecommendation]:
        """Rank all squad players by expendability (highest = most expendable).

        Computes best-11 internally from squad_scores, then scores each player.

        Formula::

            expendability = 100 - expected_points + (20 if not in best-11 else 0)

        Players at or below their position minimum are marked as protected.

        Returns a sorted list of :class:`~rehoboam.scoring.models.SellRecommendation`
        (most expendable first).
        """
        # Compute best-11 IDs from squad scores + squad players
        score_map = self.select_lineup(squad_scores)
        squad_list = list(squad_players.values())
        best_11 = select_best_eleven(squad_list, score_map)
        best_11_ids = {p.id for p in best_11}

        # Count positions to detect position minimums
        position_counts: dict[str, int] = {}
        for p in squad_list:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1

        score_lookup = {s.player_id: s for s in squad_scores}

        recommendations: list[SellRecommendation] = []
        for player in squad_list:
            ps = score_lookup.get(player.id)
            ep = ps.expected_points if ps else 0.0
            in_best_11 = player.id in best_11_ids

            bench_bonus = 0.0 if in_best_11 else 20.0

            # Tough-run penalty: if the player's next fixtures are brutal, sell
            # now before the value drops. fixture_bonus is already baked into
            # `ep`, so we apply only HALF the negative component here to tilt
            # the sort order without double-counting the signal.
            fixture_bonus = ps.fixture_bonus if ps else 0.0
            tough_run_penalty = max(0.0, -fixture_bonus) * 0.5

            expendability = 100.0 - ep + bench_bonus + tough_run_penalty

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
            if tough_run_penalty > 0:
                reason_parts.append(f"tough run ahead ({fixture_bonus:+.0f} fixture bonus)")
            if is_protected:
                reason_parts.append("position minimum")
            reason = "; ".join(reason_parts)

            recommendations.append(
                SellRecommendation(
                    score=ps if ps is not None else _dummy_score(player.id, ep),
                    player=player,
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


def _would_create_dead_weight(
    candidate_player: MarketPlayer,
    squad: list[MarketPlayer],
) -> bool:
    """True if buying *candidate_player* would saturate their position.

    A position is "saturated" when the squad already has at least as many
    players as the maximum any formation can start (1 GK, 5 DEF, 5 MID,
    3 FWD).  Adding one more guarantees someone at that position can never
    enter any starting 11 — permanent dead weight.

    Note: this doesn't depend on *who* gets displaced from the best-11.
    ``select_best_eleven`` may push out a player from a different position
    (e.g. a new GK can push out a Forward), but the dead weight is still
    the weakest player at the *candidate's* position.
    """
    pos_count = sum(1 for p in squad if p.position == candidate_player.position)
    max_starters = _POSITION_MAX_STARTERS.get(candidate_player.position, 3)
    return pos_count >= max_starters


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
        average_points=0.0,
        position="",
        lineup_probability=None,
        minutes_trend=None,
    )
