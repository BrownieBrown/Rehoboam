"""The league's Top-5 rule: finishing well costs you a player.

House rule for 26/27, applied after every matchday:

    1. the matchday winner sells his best-scoring player of that matchday
    2. second sells one of his top 2 of that matchday
    3. third sells one of his top 3
    4. fourth sells one of his top 4
    5. fifth sells one of his top 5

Sixth and below owe nothing. So the obligation is: *finish Nth, and you must
give up one of your N best performers from that matchday*.

Two consequences shape this module.

**The pool is ranked by that matchday's points, but the choice should not be.**
A player who scored 90 once and is a reliable 30 every week costs less to lose
than one who scored 85 and is a reliable 80. Which to sell is a question about
FUTURE value, so the pool is selected on matchday points and the sale is chosen
on expected points. Only the winner has no choice at all — his pool is a single
player.

**Rank determines freedom.** Finishing first is the most expensive outcome in
the league: it takes your best performer with no say in the matter. Finishing
fifth costs you the weakest of five candidates. That is worth knowing before
optimising a lineup for a big score.

The rule itself is pure — it takes standings and points a caller has already
fetched and returns a decision. Everything that touches the API or sells a
player lives below the divider at the bottom of the file, so the part that
decides can be tested without the part that acts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Places that owe a sale, and how many of your top performers are eligible.
# Index is the finishing place, value is the pool size — they happen to be
# equal, which is the whole rule in one line.
MAX_PLACE_WITH_OBLIGATION = 5


@dataclass(frozen=True)
class ForcedSale:
    """What the rule requires of us this matchday."""

    place: int
    pool: list[str]
    chosen: str
    chosen_name: str
    reason: str

    @property
    def had_a_choice(self) -> bool:
        """First place has none: the pool is exactly his best scorer."""
        return len(self.pool) > 1


def matchday_place(standings: list[dict], my_id: str) -> int | None:
    """Our 1-based finishing place for a matchday, or None if we are absent.

    ``standings`` is the ranking payload's manager list for one matchday, each
    entry carrying an id and that matchday's points. Ties are broken by keeping
    the API's own order, which is what the league sees.
    """
    ranked = sorted(
        standings,
        key=lambda m: -float(m.get("sp") or 0.0),
    )
    for index, manager in enumerate(ranked, start=1):
        if str(manager.get("i")) == str(my_id):
            return index
    return None


def eligible_pool(place: int, player_points: dict[str, float]) -> list[str]:
    """The players the rule lets us choose from, best scorer first.

    Nth place may sell any of his top N performers. Outside the top five the
    pool is empty — nothing is owed.
    """
    if place < 1 or place > MAX_PLACE_WITH_OBLIGATION:
        return []
    ordered = sorted(player_points, key=lambda pid: -player_points[pid])
    return ordered[:place]


def choose_least_damaging(
    pool: list[str],
    forward_ep: dict[str, float],
    names: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    """Which of the eligible players to give up, and why.

    Chosen on expected points from here on, NOT on what he scored that
    matchday: the rule takes a player away, and what it costs us is everything
    he would have contributed afterwards. A one-off big score is the cheapest
    thing to lose.

    A player missing from ``forward_ep`` is treated as worth 0.0 going forward,
    so an unscored player is given up before a scored one. That is deliberate —
    we should not keep a player we cannot evaluate ahead of one we can.
    """
    if not pool:
        return None
    names = names or {}
    chosen = min(pool, key=lambda pid: forward_ep.get(pid, 0.0))
    chosen_ep = forward_ep.get(chosen, 0.0)

    if len(pool) == 1:
        reason = f"first place — no choice, {names.get(chosen, chosen)} is the pool"
    else:
        kept = [p for p in pool if p != chosen]
        best_kept = max((forward_ep.get(p, 0.0) for p in kept), default=0.0)
        reason = (
            f"lowest expected points of {len(pool)} eligible "
            f"({chosen_ep:.1f} vs {best_kept:.1f} for the best we keep)"
        )
    return chosen, reason


def forced_sale(
    *,
    standings: list[dict],
    my_id: str,
    matchday_points: dict[str, float],
    forward_ep: dict[str, float],
    names: dict[str, str] | None = None,
) -> ForcedSale | None:
    """The whole rule. None when we finished outside the top five.

    ``matchday_points`` is what OUR players scored that matchday; it selects
    the pool. ``forward_ep`` is what they are worth from here; it picks the
    sale out of that pool.
    """
    place = matchday_place(standings, my_id)
    if place is None:
        logger.info("top5: we do not appear in the matchday standings")
        return None
    if place > MAX_PLACE_WITH_OBLIGATION:
        logger.info("top5: finished %d — no forced sale", place)
        return None
    if not matchday_points:
        logger.warning("top5: finished %d but no player points available", place)
        return None

    pool = eligible_pool(place, matchday_points)
    decision = choose_least_damaging(pool, forward_ep, names)
    if decision is None:
        return None
    chosen, reason = decision
    names = names or {}
    return ForcedSale(
        place=place,
        pool=pool,
        chosen=chosen,
        chosen_name=names.get(chosen, chosen),
        reason=reason,
    )


# ---------------------------------------------------------------------------
# The impure half: fetching what the rule needs, and discharging it.
# ---------------------------------------------------------------------------

PLAYED_STATUSES = (1, 3, 4, 5)


def matchday_standings(api, league, matchday: int) -> list[dict]:
    """Every manager's points for one matchday. Empty list on failure."""
    try:
        payload = api.client.session.get(
            f"{api.client.BASE_URL}/v4/leagues/{league.id}/ranking?dayNumber={matchday}"
        ).json()
        return list(payload.get("us") or [])
    except Exception:
        logger.warning("top5: could not read matchday %s standings", matchday, exc_info=True)
        return []


def squad_matchday_points(api, league, squad, matchday: int) -> dict[str, float]:
    """What each of our players scored on one matchday.

    Only players who actually took the field count: an unused substitute has no
    matchday score to be "best" at, and including him at 0.0 would put him in
    the pool ahead of nobody but still muddy the ranking.
    """
    points: dict[str, float] = {}
    for player in squad:
        try:
            perf = api.client.get_player_performance(league.id, player.id)
        except Exception:
            logger.warning("top5: no performance for %s", player.id, exc_info=True)
            continue
        for season in perf.get("it") or []:
            for match in season.get("ph") or []:
                if int(match.get("day") or 0) != matchday:
                    continue
                if match.get("st") not in PLAYED_STATUSES:
                    continue
                points[str(player.id)] = float(match.get("p") or 0.0)
    return points


def settle(
    *,
    api,
    league,
    learner,
    squad,
    forward_ep: dict[str, float],
    matchday: int,
    dry_run: bool = False,
) -> ForcedSale | None:
    """Work out the Top-5 obligation for a finished matchday and discharge it.

    Returns the decision, or None when nothing is owed or it was already
    settled. Selling is instant rather than a market listing: the obligation is
    to be rid of the player, and a listing nobody buys does not discharge it.
    """
    if learner is not None and learner.forced_sale_settled(matchday):
        logger.info("top5: matchday %d already settled", matchday)
        return None

    standings = matchday_standings(api, league, matchday)
    if not standings:
        return None

    points = squad_matchday_points(api, league, squad, matchday)
    names = {str(p.id): p.last_name for p in squad}
    decision = forced_sale(
        standings=standings,
        my_id=str(api.user.id),
        matchday_points=points,
        forward_ep=forward_ep,
        names=names,
    )
    if decision is None:
        return None

    logger.info(
        "top5: finished %d on matchday %d — selling %s (%s)",
        decision.place,
        matchday,
        decision.chosen_name,
        decision.reason,
    )

    executed = False
    if not dry_run:
        player = next((p for p in squad if str(p.id) == decision.chosen), None)
        if player is None:
            logger.error("top5: %s is not in the squad any more", decision.chosen)
        else:
            try:
                api.sell_player_instant(league, player)
                executed = True
            except Exception:
                logger.exception("top5: could not sell %s", decision.chosen_name)

    if learner is not None:
        try:
            learner.record_forced_sale(
                matchday=matchday,
                place=decision.place,
                player_id=decision.chosen,
                player_name=decision.chosen_name,
                pool=",".join(names.get(p, p) for p in decision.pool),
                reason=decision.reason,
                executed=executed,
            )
        except Exception:
            logger.exception("top5: could not record the forced sale")

    return decision
