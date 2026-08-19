"""REH-75: which `ProfitTrader` branch would have accepted a given buy.

MIRROR, NOT SOURCE. `profit_trader.py:126-190` is the authority on whether a
candidate is accepted; this module exists only to NAME the rung that decided
it, because `flip_outcomes.trend_at_buy` was never populated.
`test_branch_reconstruction.py` reconciles the two on every rung, so a change
to the shipped ladder breaks this loudly instead of relabelling silently.
"""

from __future__ import annotations

from rehoboam.replay.flip_buys import (
    FLIP_MAX_HOLD_DAYS,
    FLIP_MAX_RISK_SCORE,
    FLIP_MIN_PROFIT_PCT,
    CorpusMarketPlayer,
)

# Every label `label_for` may return, in ladder order. Not decoration:
# `label_for` validates against it, so a typo'd or newly-invented rung name
# fails loudly instead of quietly opening an extra row in the results
# document's per-branch table that nothing accounts for.
BRANCHES = (
    "low_points",
    "small_sample",
    "rising",
    "recovery",
    "dip_in_uptrend",
    "stable",
    "falling_mean_reversion",
    "secular_decline",
    "shallow_dip",
    "no_pattern",
    "below_min_profit",
    "no_trend_data",
    "too_risky",
    "mirror_divergence",
)

ELIGIBLE_BRANCHES = frozenset(
    {"rising", "recovery", "dip_in_uptrend", "stable", "falling_mean_reversion"}
)

MIN_AVG_POINTS = 20.0

# `_calculate_risk`'s scale, with its filter effectively switched off (its max
# attainable value is 100, so nothing can ever exceed this). Used to tell a
# genuine risk-filter rejection apart from a mirror/shipped-ladder
# disagreement -- see `label_for`.
RISK_DISABLED = 100.0


def reconstruct_branch(
    trend: dict,
    average_points: float,
    *,
    market_value: int,
    min_profit_pct: float = FLIP_MIN_PROFIT_PCT,
) -> tuple[str, float]:
    """Name the rung that decides this candidate, and its expected appreciation.

    Rung order is the shipped order; it is what makes the label causal rather
    than merely descriptive. `market_value` is required (not merely accepted)
    because `current_value` falls back to it -- `profit_trader.py:114` -- and
    a wrong fallback fabricates a bogus below-peak reading (see the
    `current_value` regression test).

    NOT modelled here: the pre-ladder skips the shipped code applies before
    reaching this logic at all -- `player.status != 0` and the affordability
    gate (`profit_trader.py:93-101`). Defensible for buys that actually
    executed (they must have passed both to exist), but this function alone
    cannot see either one.

    Nor are the gates the LIVE BUY PATH applies around the ladder: only
    listings passing `is_kickbase_seller()` are ever considered (`trader.py:685`
    -- the hardest provenance gate of all), `max_opportunities` caps bids at
    5-10 per session (`trader.py:719`), and `BidEvaluator` cancels a flip bid
    more than 25% over market value (`bid_evaluator.py:116-119`). Each of these
    shrinks the truly-eligible set, all in the same direction, so an eligible
    count derived from this function is an UPPER BOUND on the set -- never a
    bound on any sum over it.

    The STATISTIC the ladder's points gates read was also wrong until REH-77.
    Callers supply `average_points` from `replay.flip_buys.average_points_at`,
    which returned a CAREER mean per appearance where the shipped path reads
    Kickbase's season-to-date `ap` (`kickbase_client.py:88`). It is now narrowed
    to the season and verified against the eight recorded `ap` values. Note the
    mirror reconciliation could never have caught this: it feeds both sides the
    same value, so a wrong input agrees with itself. Figures published before
    2026-08-19 were produced with the career mean.
    """
    if not trend.get("has_data", False):
        return "no_trend_data", 0.0

    trend_direction = trend.get("trend", "unknown")
    trend_pct = trend.get("trend_pct", 0)
    current_value = trend.get("current_value", market_value)
    peak_value = trend.get("peak_value", 0)

    if average_points < MIN_AVG_POINTS:
        return "low_points", 0.0
    if average_points > 80 and trend_pct > 40:
        return "small_sample", 0.0

    if trend_direction == "rising" and trend_pct > 5:
        branch, appreciation = "rising", min(trend_pct, 20)
    elif trend.get("is_recovery", False) and average_points >= 30:
        branch, appreciation = "recovery", 12.0
    elif trend.get("is_dip_in_uptrend", False) and average_points >= 30:
        branch, appreciation = "dip_in_uptrend", 10.0
    elif trend_direction == "stable" and average_points >= 40:
        branch, appreciation = "stable", 8.0
    elif trend_direction == "falling" and peak_value > 0:
        if trend.get("is_secular_decline", False):
            return "secular_decline", 0.0
        current_vs_peak_pct = ((current_value - peak_value) / peak_value) * 100
        if current_vs_peak_pct < -25 and average_points >= 40:
            branch = "falling_mean_reversion"
            appreciation = min(abs(current_vs_peak_pct) * 0.3, 15)
        else:
            return "shallow_dip", 0.0
    else:
        return "no_pattern", 0.0

    if appreciation < min_profit_pct:
        return "below_min_profit", float(appreciation)
    return branch, float(appreciation)


def shipped_opportunity(
    trend: dict,
    average_points: float,
    market_value: int,
    *,
    max_risk_score: float = FLIP_MAX_RISK_SCORE,
):
    """The shipped `ProfitTrader`'s `ProfitOpportunity` for one candidate, or
    `None` if it rejected the candidate.

    The single call site into shipped code that `profit_trader_accepts` and
    the reconciliation test both build on, so there is exactly one place that
    constructs `ProfitTrader` and its `CorpusMarketPlayer` stand-in.

    `price == market_value` because `ProfitTrader` BRANCHES on that equality
    (profit_trader.py:121) -- feeding anything else sends the candidate down
    the non-Kickbase path where `value_gap` is negative and it is dropped, and
    the reconciliation would pass vacuously with everything rejected.

    `position` is PINNED to "Midfielder" and is not reconstructed. The corpus
    does carry positions, but feeding the real one would change
    `_calculate_risk` (Goalkeeper +10, profit_trader.py:313-314) and therefore
    could change a published label, so the pin is deliberate rather than
    pending. Its one consequence: the maximum risk score attainable under this
    harness is **45** (falling +30, avg_points<40 +15), not the 55 a Goalkeeper
    could reach -- see `label_for`'s docstring, which depends on this number.
    Feeding a real position would need a re-run and a fresh determinism gate.
    """
    from rehoboam.profit_trader import ProfitTrader

    trader = ProfitTrader(
        min_profit_pct=FLIP_MIN_PROFIT_PCT,
        max_hold_days=FLIP_MAX_HOLD_DAYS,
        max_risk_score=max_risk_score,
    )
    player = CorpusMarketPlayer(
        id="reconcile",
        price=market_value,
        market_value=market_value,
        average_points=average_points,
        position="Midfielder",
    )
    opportunities = trader.find_profit_opportunities(
        market_players=[player],
        current_budget=market_value * 10,
        player_trends={"reconcile": trend},
        team_value=market_value * 10,
    )
    return opportunities[0] if opportunities else None


def profit_trader_accepts(
    trend: dict,
    average_points: float,
    market_value: int,
    *,
    max_risk_score: float = FLIP_MAX_RISK_SCORE,
) -> bool:
    """Whether the shipped `ProfitTrader` accepts this candidate.

    `max_risk_score` defaults to the live flip threshold; `label_for` passes
    `RISK_DISABLED` on a second call to tell a genuine risk rejection apart
    from a mirror/ladder disagreement.
    """
    return (
        shipped_opportunity(trend, average_points, market_value, max_risk_score=max_risk_score)
        is not None
    )


def label_for(trend: dict, average_points: float, market_value: int) -> str:
    """The branch label for one buy: the ladder rung, or why it was rejected.

    Eligibility is the SHIPPED verdict, never the mirror's. `ProfitTrader`
    applies `_calculate_risk` after the ladder (profit_trader.py:214-217), a
    heuristic this module must not reimplement.

    `too_risky` is UNREACHABLE here, and by two margins rather than one. The
    maximum risk score a ladder-accepted candidate can reach IN PRINCIPLE is 55
    (falling +30, avg_points<40 +15, Goalkeeper +10 -- the value-gap term is
    always 0 because a ladder-accepted `expected_appreciation` is capped at 20,
    below its +10 threshold of 30), against `FLIP_MAX_RISK_SCORE = 60.0`. Under
    THIS harness the ceiling is lower still: `shipped_opportunity` pins
    `position="Midfielder"`, so the Goalkeeper +10 is unreachable and the real
    maximum is **45**. Both are below 60, so the label is correct today either
    way -- but if `FLIP_MAX_RISK_SCORE` is ever re-tuned to 50, as the 55 figure
    anticipates, the pinned position would make this reconstruction disagree
    with the live path. The label exists for that re-tuning, not because it
    fires today.

    So when the ladder accepts but the shipped trader still rejects, this
    re-checks with risk effectively disabled (`RISK_DISABLED`). If THAT still
    rejects, the rejection had nothing to do with risk -- the mirror and the
    shipped ladder have diverged, which is a DEFECT in this module, not a
    market outcome, and is labelled `mirror_divergence`. A reader of the
    per-branch table must never interpret `mirror_divergence` as a rejection
    cause: it means this module needs fixing, not that the candidate was bad.
    """
    branch, _ = reconstruct_branch(trend, average_points, market_value=market_value)
    if branch not in BRANCHES:
        raise ValueError(f"reconstruct_branch returned an unknown rung: {branch!r}")
    if branch not in ELIGIBLE_BRANCHES:
        return branch
    if profit_trader_accepts(trend, average_points, market_value):
        return branch
    if profit_trader_accepts(trend, average_points, market_value, max_risk_score=RISK_DISABLED):
        return "too_risky"
    return "mirror_divergence"
