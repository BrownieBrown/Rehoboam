"""The premium that maximises expected value, with the alternative priced in.

A bid at premium p wins with the share F(p) of listings that cleared at or
below p in the player's price band (`WinCurve`), and pays p x market value
when it does. What winning is WORTH is the gain over the next-best
alternative the bot could buy instead — not the whole gain: a strong player
with an equivalent rival listing is worth almost nothing to win at any
premium, a unique one is worth a lot. Points are priced at what the league
pays per expected point (the price-on-points fit behind `fair_price`).

    EV(p) = F(p) x (eur_per_point x unique_gain - p/100 x market_value)

Measured 2026-09-23 on the real curves with EUR 255,008 per point: without
the alternative term every large gain pinned to the top of the curve (Burger
+19%, a 114-point must-have +34%) — the behaviour that lost money. Against
Schick as the runner-up, Burger's unique gain is 5.3 points and the optimum
is about +3%, which is what the league's profitable managers pay.

Pure. None when the curve has no evidence, the price per point is unknown,
or the unique gain is not positive — the bidder falls back to reading the
curve at the tier's quantile.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Kickbase's penalty for an empty lineup slot. When the squad cannot field
#: eleven and kickoff is close, winning is worth this many points more.
EMPTY_SLOT_PENALTY_POINTS = 100.0


@dataclass(frozen=True)
class ValuePoint:
    premium_pct: float
    win_share: float
    expected_value_eur: float
    unique_gain_pts: float
    value_eur: float  # eur_per_point x unique gain


def optimal_premium(
    win_curve,
    *,
    market_value: int,
    unique_gain_pts: float,
    eur_per_point: float | None,
    step_pct: float = 0.5,
) -> ValuePoint | None:
    """The premium on the band's curve with the highest expected value.

    Scans premiums from 0 to the top of the band's curve in `step_pct` steps;
    F(p) is read off the curve at each. Ties go to the lower premium.
    """
    if win_curve is None or eur_per_point is None or eur_per_point <= 0:
        return None
    if unique_gain_pts <= 0 or market_value <= 0:
        return None
    top = win_curve.premium_at(int(market_value), 1.0)
    if top is None:
        return None
    value = float(eur_per_point) * float(unique_gain_pts)
    best: ValuePoint | None = None
    p = 0.0
    while p <= top.premium_pct + 1e-9:
        share = win_curve.win_share(int(market_value), p)
        if share is None:
            return None
        ev = share * (value - p / 100.0 * market_value)
        if best is None or ev > best.expected_value_eur + 1e-6:
            best = ValuePoint(
                premium_pct=p,
                win_share=share,
                expected_value_eur=ev,
                unique_gain_pts=float(unique_gain_pts),
                value_eur=value,
            )
        p += step_pct
    return best
