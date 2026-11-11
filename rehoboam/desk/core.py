"""The core book: the eleven, judged on points (ADR-0008).

The core is every player we hold that we did not buy as a flip. Its desired
state is the best legal eleven; the session sells any core player the eleven
no longer uses. This module picks the one upgrade a session may bid on.

An upgrade is a listing that raises the eleven's expected points by at least
`CORE_MIN_GAIN` and pays for itself under the planner's own exchange rate
(ADR-0003, §4.3): the points it adds, plus μ times its expected drift, minus μ
times its premium and the drift of the starter it pushes out. One a session,
and only one that can play the next kickoff, so the gain it is bought for is
real. The displaced starter is sold by the next session, so the core never
sells before its replacement has arrived (C1).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rehoboam.desk.eleven import ELEVEN, best_eleven
from rehoboam.desk.model import Offer, Player

# Expected points a matchday an upgrade must add. A premium buys one move; a
# smaller gain is within the scorer's error and invites the churn of M2-13.
CORE_MIN_GAIN = 10.0
HOLE_RANK = 1e9  # a cheap fill outranks every upgrade


def upgrade(
    squad: Sequence[Player],
    offers: Sequence[Offer],
    mu: float,
    *,
    cash: int,
    allowance: float,
    holds_value: bool = False,
    capital_neutral: bool = False,
    surplus: int = 0,
    fill_holes: bool = False,
    others: Sequence[Player] = (),
    cheap_fill: bool = False,
    fills_only: bool = False,
) -> Offer | None:
    """The bid that raises U the most through the eleven, or None.

    ``cash`` is the wallet net of open bids and this session's sales; a bid may
    take it to ``-allowance`` if it can be sold before the kickoff, else to zero.
    ``holds_value`` buys only an upgrade whose expected drift covers its premium:
    points that do not cost money, the leaders' quality buys (M2-16, M2-20).
    ``capital_neutral`` buys only an upgrade whose bid is at most what the
    starters it displaces sell for: the flip book's capital is not drawn on, and
    the core improves by buying points cheaper than it sells them (M2-21). Filling
    an empty slot is exempt: there is nobody to displace, and the slot costs
    -100 points every matchday it stays empty (M2-23). ``fill_holes`` turns the
    exemption on; ``others`` are the rest of the squad (the flips), so only a hole
    in the eleven the whole squad can field counts. Without them a hole the flips
    cover still counts, and the core converts flip slots into core starters.
    ``cheap_fill`` fills a hole with the most expected points per euro of bid,
    not the most utility: a star bought to fill a slot ties up capital the flip
    book trades with (M2-25).
    ``surplus`` is positive cash the core may add on top: the week's flip profit,
    back in the wallet after the repayment before a kickoff. Never debt (M2-22).
    """
    base_ep, base = best_eleven(squad)
    starters = {p.player_id for p in base}
    fielded = len(best_eleven([*squad, *others])[1])
    best: tuple[float, Offer] | None = None
    for o in offers:
        if not o.arrives_before_kickoff or (holds_value and o.player.drift < o.premium):
            continue
        if cash - o.bid < (-allowance if o.sellable_before_kickoff else 0):
            continue
        ep, eleven = best_eleven([*squad, o.player])
        if ep - base_ep < CORE_MIN_GAIN:
            continue
        kept = {p.player_id for p in eleven}
        leaving = [p for p in squad if p.player_id in starters - kept]
        fills_a_hole = (  # an empty slot is -100 every matchday it stays
            fill_holes and len(best_eleven([*squad, *others, o.player])[1]) > fielded
        )
        if fills_only and not fills_a_hole:
            continue
        if (
            capital_neutral
            and not fills_a_hole
            and o.bid > sum(p.market_value for p in leaving) + max(0, surplus)
        ):
            continue
        displaced = sum(p.market_value * p.drift for p in leaving)
        value = (
            ep - base_ep + mu * (o.player.market_value * o.player.drift - o.premium_eur - displaced)
        )
        if value <= 0:
            continue
        if cheap_fill and fills_a_hole:  # fills outrank upgrades, cheapest per point first
            value = HOLE_RANK + (ep - base_ep) / max(1, o.bid)
        if best is None or value > best[0]:
            best = (value, o)
    return best[1] if best else None


def bids(
    squad: Sequence[Player],
    offers: Sequence[Offer],
    mu: float,
    *,
    cash: int,
    allowance: float,
    others: Sequence[Player] = (),
    pending: Sequence[Player] = (),
    **rules: Any,
) -> tuple[Offer, ...]:
    """The core's bids this session (ADR-0011, M2-26).

    Every slot the squad cannot field is filled now, not one a session: from an
    empty squad (the reset's day 0) the eleven has to stand before the first
    kickoff. Open core bids (``pending``) count as filled, so a slot is not
    bought twice; ``others`` (the flips) count for fielding. Only in a session
    with no hole to fill, one upgrade, as before.
    """
    chosen: list[Offer] = []
    pool = list(offers)
    while True:
        team = [*squad, *pending, *(o.player for o in chosen)]
        if len(best_eleven([*team, *others])[1]) >= ELEVEN:
            break
        fill = upgrade(
            team,
            pool,
            mu,
            cash=cash,
            allowance=allowance,
            others=others,
            fills_only=True,
            **{**rules, "fill_holes": True},
        )
        if fill is None:
            break
        chosen.append(fill)
        pool.remove(fill)
        cash -= fill.bid
    if chosen or not offers:
        return tuple(chosen)
    one = upgrade(squad, pool, mu, cash=cash, allowance=allowance, others=others, **rules)
    return (one,) if one is not None else ()
