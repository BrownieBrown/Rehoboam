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

**Refinements (M2-11).** A table may be fitted with an extra level above the
full cell, keyed by the 14-night move and/or the points of the last match. A
refined cell shrinks toward the full cell, so a table without evidence for a
refinement predicts what the unrefined table predicts. They exist because the
hold model missed rebounds: players sold after a 14-night fall, or while out of
the team, rose a mean +30–43% in the week after (spec §12, M2-11).
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, median

# Bucket edges are lower-inclusive: a value equal to an edge falls in the bucket
# above it. The edges were set before the first fit (spec §4.2) and are not
# tuned on the test window.
LAST_MOVE_EDGES: tuple[float, ...] = (-0.02, -0.005, 0.005, 0.02)
MOVE_3N_EDGES: tuple[float, ...] = (-0.05, -0.015, 0.015, 0.05)
BAND_EDGES: tuple[int, ...] = (5_000_000, 15_000_000)

# Refinement edges. The 14-night edges are the quintiles of the 2025/26 training
# set, rounded; two quintiles coincide at 0 (players at the floor never move), so
# there are four buckets. The points edges follow Eval §7.3 (150+ rose +4.8%).
MOVE_14N_EDGES: tuple[float, ...] = (-0.18, 0.0, 0.10)
LAST_POINTS_EDGES: tuple[int, ...] = (50, 100, 150)
LAST_POINTS_WINDOW_DAYS = 10  # an older match counts as "no recent match"
MOVE_14N = "move_14n"
LAST_POINTS = "last_points"
REFINEMENTS: tuple[str, ...] = (MOVE_14N, LAST_POINTS)  # canonical order of the key

SHRINK_K = 30
LOW_QUANTILE = 0.10
MIN_QUANTILE_N = 30
MARKET_NIGHTS = 3
MARKET_MIN_VALUE = 1_000_000  # the universe median ignores the cheapest tail

Key = tuple[int, ...]
Matches = Sequence[tuple[date, int]]  # (value day the result is known, points), sorted


@dataclass(frozen=True)
class DriftFeatures:
    last_move: float  # last nightly change, as a fraction (0.01 = +1%)
    move_3n: float  # change over the last three nights
    market_value: int
    market_falling: bool
    move_14n: float | None = None  # None without 14 nights of history
    last_points: int | None = None  # the last match known that day; None = none in the window


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
    refine: tuple[str, ...] = ()

    def predict(self, features: DriftFeatures) -> DriftEstimate:
        keys = cell_keys(features, self.refine)  # most specific first
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


def cell_keys(f: DriftFeatures, refine: Sequence[str] = ()) -> tuple[Key, ...]:
    """The cell hierarchy, most specific first.

    With ``refine``, a refined cell sits above the full cell. It is left out when
    a refined feature is unknown (a path shorter than 14 nights), so such a
    player is predicted by the unrefined hierarchy.
    """
    lm = bisect_right(LAST_MOVE_EDGES, f.last_move)
    m3 = bisect_right(MOVE_3N_EDGES, f.move_3n)
    b = band(f.market_value)
    mk = int(f.market_falling)
    keys: tuple[Key, ...] = ((lm, m3, b, mk), (lm, b, mk), (b, mk), ())
    extra = _refined(f, refine)
    return keys if extra is None else ((*keys[0], *extra), *keys)


def _refined(f: DriftFeatures, refine: Sequence[str]) -> Key | None:
    if not refine:
        return None
    extra: list[int] = []
    for name in refine:
        if name == MOVE_14N:
            if f.move_14n is None:
                return None
            extra.append(bisect_right(MOVE_14N_EDGES, f.move_14n))
        elif name == LAST_POINTS:
            points = f.last_points  # None: no match in the window, bucket 0
            extra.append(0 if points is None else 1 + bisect_right(LAST_POINTS_EDGES, points))
        else:
            raise ValueError(f"unknown refinement {name!r}")
    return tuple(extra)


def canonical_refine(refine: Iterable[str]) -> tuple[str, ...]:
    """The refinements in key order; raises on an unknown name."""
    names = set(refine)
    unknown = names - set(REFINEMENTS)
    if unknown:
        raise ValueError(f"unknown refinement {sorted(unknown)[0]!r}")
    return tuple(name for name in REFINEMENTS if name in names)


def fit(
    samples: Iterable[tuple[DriftFeatures, float]],
    *,
    lag: int,
    horizon: int,
    refine: Iterable[str] = (),
) -> DriftTable:
    refine = canonical_refine(refine)
    grouped: dict[Key, list[float]] = defaultdict(list)
    for features, target in samples:
        for key in cell_keys(features, refine):
            grouped[key].append(target)
    if () not in grouped:
        raise ValueError("cannot fit a drift table on no samples")
    cells = {key: _stats(values) for key, values in grouped.items()}
    return DriftTable(lag=lag, horizon=horizon, cells=cells, refine=refine)


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


def features_at(
    path: Path, day: date, market_falling: bool | None, last_points: int | None = None
) -> DriftFeatures | None:
    """The features known at ``day`` (its value has been published), or None.

    ``last_points`` comes from `last_points_at`; the path itself gives the rest.
    """
    if market_falling is None:
        return None
    today = path.get(day)
    yesterday = path.get(day - timedelta(days=1))
    three_back = path.get(day - timedelta(days=3))
    if not today or not yesterday or not three_back:
        return None
    fourteen_back = path.get(day - timedelta(days=14))
    return DriftFeatures(
        last_move=today / yesterday - 1,
        move_3n=today / three_back - 1,
        market_value=today,
        market_falling=market_falling,
        move_14n=today / fourteen_back - 1 if fourteen_back else None,
        last_points=last_points,
    )


def last_points_at(matches: Matches, day: date) -> int | None:
    """Points of the newest match whose result was known before ``day``.

    A match is known on the value day of its final whistle, and it is priced
    by the next nightly update, so a result on ``day`` itself does not count.
    None when there is none within `LAST_POINTS_WINDOW_DAYS`.
    """
    i = bisect_left(matches, (day,))  # first match known on or after `day`
    if i == 0:
        return None
    known, points = matches[i - 1]
    return points if (day - known).days <= LAST_POINTS_WINDOW_DAYS else None


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
    matches: Mapping[str, Matches] | None = None,
) -> Iterable[tuple[DriftFeatures, float]]:
    """Every (features, target) pair whose features are known in [first_day, last_day].

    Pass ``last_day`` as the fit's cutoff minus ``lag + horizon``, so no target
    reaches past the cutoff. Pass ``matches`` to fit a `LAST_POINTS` refinement:
    without it every sample reads as "no recent match".
    """
    for player_id, path in paths.items():
        played = matches.get(player_id, ()) if matches is not None else ()
        for day in path:
            if not first_day <= day <= last_day:
                continue
            points = last_points_at(played, day) if played else None
            features = features_at(path, day, market_falling(moves, day), points)
            target = target_at(path, day, lag=lag, horizon=horizon)
            if features is not None and target is not None:
                yield features, target
