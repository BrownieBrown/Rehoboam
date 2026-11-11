"""Drift: the expected market-value change over the next days (spec v3 §4.2).

A conditional-mean lookup table, not a fitted parametric model. Each sample is
the market-value path of one player from one day. Its cell is the last nightly
move, the three-night move, the price band and whether the market as a whole is
falling. Its target is the change from ``lag`` days ahead to ``lag + horizon``
days ahead:

- ``lag=1`` values a listing: the auction settles after the next nightly update,
  so the buyer's path starts there.
- ``lag=0`` values a holding, whose path starts now.

A thin cell borrows from its parent, ``(n·cell + k·parent) / (n + k)``. The
hierarchy is full cell → (last move, band, market) → (band, market) → all, so a
rare combination predicts close to what its band does. The low quantile (p10)
comes from the most specific level with enough samples; it is used as the
haircut on a named sale in the solvency check.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, median

# Bucket edges are lower-inclusive: a value equal to an edge falls in the bucket
# above it. The edges were set before the first fit (spec §4.2) and are not
# tuned on the test window.
LAST_MOVE_EDGES: tuple[float, ...] = (-0.02, -0.005, 0.005, 0.02)
MOVE_3N_EDGES: tuple[float, ...] = (-0.05, -0.015, 0.015, 0.05)
BAND_EDGES: tuple[int, ...] = (5_000_000, 15_000_000)

SHRINK_K = 30
LOW_QUANTILE = 0.10
MIN_QUANTILE_N = 30
MARKET_NIGHTS = 3
MARKET_MIN_VALUE = 1_000_000  # the universe median ignores the cheapest tail

Key = tuple[int, ...]


@dataclass(frozen=True)
class DriftFeatures:
    last_move: float  # last nightly change, as a fraction (0.01 = +1%)
    move_3n: float  # change over the last three nights
    market_value: int
    market_falling: bool


@dataclass(frozen=True)
class DriftEstimate:
    mean: float  # expected change over the horizon, as a fraction
    low: float  # its p10
    n: int  # samples in the most specific cell


@dataclass(frozen=True)
class CellStats:
    n: int
    mean: float
    low: float  # NaN when n < MIN_QUANTILE_N


@dataclass(frozen=True)
class DriftTable:
    lag: int
    horizon: int
    cells: Mapping[Key, CellStats]

    def predict(self, features: DriftFeatures) -> DriftEstimate:
        keys = cell_keys(features)  # most specific first
        estimate = self.cells[()].mean
        for key in reversed(keys[:-1]):  # general → specific
            stats = self.cells.get(key)
            if stats is not None:
                estimate = (stats.n * stats.mean + SHRINK_K * estimate) / (stats.n + SHRINK_K)
        return DriftEstimate(mean=estimate, low=self._low(keys), n=self._n(keys[0]))

    def _low(self, keys: tuple[Key, ...]) -> float:
        for key in keys:
            stats = self.cells.get(key)
            if stats is not None and stats.n >= MIN_QUANTILE_N:
                return stats.low
        return self.cells[()].low

    def _n(self, key: Key) -> int:
        stats = self.cells.get(key)
        return stats.n if stats is not None else 0


def band(market_value: int) -> int:
    """0 = under 5m, 1 = 5–15m, 2 = 15m and up."""
    return bisect_right(BAND_EDGES, market_value)


def cell_keys(f: DriftFeatures) -> tuple[Key, ...]:
    lm = bisect_right(LAST_MOVE_EDGES, f.last_move)
    m3 = bisect_right(MOVE_3N_EDGES, f.move_3n)
    b = band(f.market_value)
    mk = int(f.market_falling)
    return ((lm, m3, b, mk), (lm, b, mk), (b, mk), ())


def fit(samples: Iterable[tuple[DriftFeatures, float]], *, lag: int, horizon: int) -> DriftTable:
    grouped: dict[Key, list[float]] = defaultdict(list)
    for features, target in samples:
        for key in cell_keys(features):
            grouped[key].append(target)
    if () not in grouped:
        raise ValueError("cannot fit a drift table on no samples")
    cells = {key: _stats(values) for key, values in grouped.items()}
    return DriftTable(lag=lag, horizon=horizon, cells=cells)


def _stats(values: list[float]) -> CellStats:
    low = _quantile(values, LOW_QUANTILE) if len(values) >= MIN_QUANTILE_N else math.nan
    return CellStats(n=len(values), mean=mean(values), low=low)


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


# ---------------------------------------------------------------------------
# Features and targets from a value path
# ---------------------------------------------------------------------------

Path = Mapping[date, int]


def features_at(path: Path, day: date, market_falling: bool | None) -> DriftFeatures | None:
    """The features known at ``day`` (its value has been published), or None."""
    if market_falling is None:
        return None
    today = path.get(day)
    yesterday = path.get(day - timedelta(days=1))
    three_back = path.get(day - timedelta(days=3))
    if not today or not yesterday or not three_back:
        return None
    return DriftFeatures(
        last_move=today / yesterday - 1,
        move_3n=today / three_back - 1,
        market_value=today,
        market_falling=market_falling,
    )


def target_at(path: Path, day: date, *, lag: int, horizon: int) -> float | None:
    start = path.get(day + timedelta(days=lag))
    end = path.get(day + timedelta(days=lag + horizon))
    if not start or not end:
        return None
    return end / start - 1


def market_moves(paths: Mapping[str, Path]) -> dict[date, float]:
    """The universe's median nightly move per day, over players worth 1m or more."""
    moves: dict[date, list[float]] = defaultdict(list)
    for path in paths.values():
        for day, value in path.items():
            previous = path.get(day - timedelta(days=1))
            if previous and previous >= MARKET_MIN_VALUE and value:
                moves[day].append(value / previous - 1)
    return {day: median(values) for day, values in moves.items()}


def market_falling(moves: Mapping[date, float], day: date) -> bool | None:
    """True when the mean of the last MARKET_NIGHTS daily medians is below zero."""
    window = [moves.get(day - timedelta(days=i)) for i in range(MARKET_NIGHTS)]
    if any(m is None for m in window):
        return None
    return mean(m for m in window if m is not None) < 0


def training_samples(
    paths: Mapping[str, Path],
    moves: Mapping[date, float],
    *,
    lag: int,
    horizon: int,
    first_day: date,
    last_day: date,
) -> Iterable[tuple[DriftFeatures, float]]:
    """Every (features, target) pair whose features are known in [first_day, last_day].

    Pass ``last_day`` as the fit's cutoff minus ``lag + horizon``, so no target
    reaches past the cutoff.
    """
    for path in paths.values():
        for day in path:
            if not first_day <= day <= last_day:
                continue
            features = features_at(path, day, market_falling(moves, day))
            target = target_at(path, day, lag=lag, horizon=horizon)
            if features is not None and target is not None:
                yield features, target
