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
)

ELIGIBLE_BRANCHES = frozenset(
    {"rising", "recovery", "dip_in_uptrend", "stable", "falling_mean_reversion"}
)

MIN_AVG_POINTS = 20.0


def reconstruct_branch(
    trend: dict,
    average_points: float,
    *,
    min_profit_pct: float = FLIP_MIN_PROFIT_PCT,
) -> tuple[str, float]:
    """Name the rung that decides this candidate, and its expected appreciation.

    Rung order is the shipped order; it is what makes the label causal rather
    than merely descriptive.
    """
    if not trend.get("has_data", False):
        return "no_trend_data", 0.0

    trend_direction = trend.get("trend", "unknown")
    trend_pct = trend.get("trend_pct", 0)
    current_value = trend.get("current_value", 0)
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


def profit_trader_accepts(trend: dict, average_points: float, market_value: int) -> bool:
    """The shipped verdict, for reconciling against `reconstruct_branch`.

    `price == market_value` because `ProfitTrader` BRANCHES on that equality
    (profit_trader.py:121) -- feeding anything else sends the candidate down
    the non-Kickbase path where `value_gap` is negative and it is dropped, and
    the reconciliation would pass vacuously with everything rejected.
    """
    from rehoboam.profit_trader import ProfitTrader

    trader = ProfitTrader(
        min_profit_pct=FLIP_MIN_PROFIT_PCT,
        max_hold_days=FLIP_MAX_HOLD_DAYS,
        max_risk_score=FLIP_MAX_RISK_SCORE,
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
    return bool(opportunities)


def label_for(trend: dict, average_points: float, market_value: int) -> str:
    """The branch label for one buy: the ladder rung, or why it was rejected.

    Eligibility is the SHIPPED verdict, never the mirror's. `ProfitTrader`
    applies `_calculate_risk` after the ladder (profit_trader.py:214-217), a
    heuristic this module must not reimplement -- so a candidate the ladder
    accepts can still be rejected on risk. That case gets its own label rather
    than being counted as an eligible branch, which would overstate how much
    money each rung actually sourced.
    """
    branch, _ = reconstruct_branch(trend, average_points)
    if branch not in ELIGIBLE_BRANCHES:
        return branch
    if not profit_trader_accepts(trend, average_points, market_value):
        return "too_risky"
    return branch
