"""The exchange rate between euros and points (spec v3 §4.3).

The planner's objective adds the expected points of the kickoff eleven to the
money the squad's drift earns, net of premiums, so it needs a rate. Money is
worth the points it can still buy. P, the market's price of one point per match
of player quality (`store/transfer_study.eur_per_point`, 255,008 € on
2026-09-23), buys one point in every matchday that is left. The reset clears
budgets, so money carried into it is worth nothing.

This is the one dial that sets the bot's character. A high μ makes it a trader
that buys points only when they are cheap. A low μ makes it a manager that
spends for this week's eleven. The backtest sweeps ``scale`` over ×0.5, ×1 and
×2, and the bar (§8.4, B4) must hold across the sweep.
"""

from __future__ import annotations


def points_per_euro(matchdays_left: int, eur_per_point: float, *, scale: float = 1.0) -> float:
    """μ(N): how many points of the next kickoff's eleven one euro is worth.

    Args:
        matchdays_left: N, the matchdays before the reset (or the season's end),
            counting the next one. 1 means the next kickoff is the last before the
            reset.
        eur_per_point: P, the market's price of one point per match of quality.
        scale: ``MU_SCALE``, the backtest's sweep.
    """
    # Bad input raises instead of defaulting. A silent 0 would make every euro
    # worthless and turn the planner into an all-in spender; a silent default P
    # would hide a broken fit. The caller (the session) decides what an unknown
    # schedule means. It must never be a number guessed here.
    if matchdays_left < 1:
        raise ValueError(f"matchdays_left must be at least 1, got {matchdays_left}")
    if not eur_per_point > 0:  # also rejects NaN
        raise ValueError(f"eur_per_point must be positive, got {eur_per_point}")
    if not scale >= 0:
        raise ValueError(f"scale must be non-negative, got {scale}")
    return (matchdays_left - 1) / eur_per_point * scale
