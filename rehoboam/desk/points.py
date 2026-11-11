"""Expected points with the market price folded in (spec v3 §4.1, ADR-0002).

The scorer ranks the league well but overrates exactly the players a planner
picks. Among players predicted at 60+ points, ours were over-predicted by +26.7
and the rest by +0.8. Bias rises with EP per euro, from −18.1 to +19.0. In the
W1 backtest the planner's elevens scored 144–289 points a matchday below their
EP, against +46 for our real squads (spec §12). Market value carries what the
scorer misses, so the planner reads

    EP' = max(0, a + b · EP + c · ln(market value))

refitted by least squares on the matchdays that have kicked off before the
moment it is used, never later ones.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

FIT_MIN_EP = 10.0  # the fit population: players the scorer expects to play at all
MIN_ROWS = 100


@dataclass(frozen=True)
class PriceBlend:
    intercept: float
    ep_weight: float
    log_value_weight: float
    rows: int

    def apply(self, ep: float, market_value: int) -> float:
        if market_value <= 0:
            return ep
        blended = (
            self.intercept + self.ep_weight * ep + self.log_value_weight * math.log(market_value)
        )
        return max(0.0, blended)


def fit_blend(rows: Iterable[tuple[float, int, float]]) -> PriceBlend | None:
    """Least squares of points on (EP, ln value) over (EP, value, points) rows."""
    xs: list[tuple[float, float, float]] = []
    ys: list[float] = []
    for ep, value, points in rows:
        if ep >= FIT_MIN_EP and value > 0:
            xs.append((1.0, ep, math.log(value)))
            ys.append(points)
    if len(xs) < MIN_ROWS:
        return None
    coef = _solve(
        [[sum(x[i] * x[j] for x in xs) for j in range(3)] for i in range(3)],
        [sum(x[i] * y for x, y in zip(xs, ys, strict=True)) for i in range(3)],
    )
    if coef is None:
        return None
    return PriceBlend(coef[0], coef[1], coef[2], len(xs))


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            factor = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    out = [0.0] * n
    for r in reversed(range(n)):
        out[r] = (m[r][n] - sum(m[r][c] * out[c] for c in range(r + 1, n))) / m[r][r]
    return out
