"""REH-75: where the money went across every completed round trip.

`flip_outcomes` is a table of ROUND TRIPS, not of flips. `backfill.py`'s
`_pair_flips` FIFO-pairs every buy against every later sell per `player_id`,
and `LearningTracker.record_flip_outcome` fires on every instant sell. Neither
consults the motive for the buy, so an EP-driven squad buy that was later sold
is indistinguishable here from a `ProfitTrader` flip. Nothing in this module
may attribute a sum to the flip channel -- see the design doc's opening
section for why that claim is not available from this data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RoundTrip:
    """One completed buy->sell pair, as `flip_outcomes` records it."""

    trip_id: int
    player_id: str
    player_name: str
    buy_price: int
    sell_price: int
    buy_date: float
    sell_date: float
    hold_days: int

    @property
    def realised(self) -> int:
        return self.sell_price - self.buy_price


@dataclass(frozen=True)
class Decomposition:
    """The identity's three terms, in euros.

    `entry_premium` is stored UNNEGATED -- what we paid above market value --
    and enters `total` negated, exactly as it enters the identity. The
    pre-registered dominance rule compares `-entry_premium` against the other
    two terms, so storing it pre-negated would silently flip the winner.
    """

    selection: int
    exit_timing: int
    entry_premium: int

    @property
    def total(self) -> int:
        return self.selection + self.exit_timing - self.entry_premium

    def __add__(self, other: Decomposition) -> Decomposition:
        return Decomposition(
            selection=self.selection + other.selection,
            exit_timing=self.exit_timing + other.exit_timing,
            entry_premium=self.entry_premium + other.entry_premium,
        )


def decompose(trip: RoundTrip, *, mv_buy: int, mv_h: int) -> Decomposition:
    """Split a round trip's realised P&L into SELECTION + EXIT - ENTRY PREMIUM.

    An identity, not an estimate: the terms cancel to `sell_price - buy_price`.
    """
    return Decomposition(
        selection=mv_h - mv_buy,
        exit_timing=trip.sell_price - mv_h,
        entry_premium=trip.buy_price - mv_buy,
    )


SECONDS_PER_DAY = 86400.0

# Corpus snapshots are daily, and every round trip in scope resolves to within
# 0.99 days of every horizon (measured during design). Three days is therefore
# a guard against a future rerun over sparser data, not a threshold this run
# relies on.
DEFAULT_MAX_GAP_DAYS = 3.0


def mv_nearest(
    db_path: Path,
    player_id: str,
    at: float,
    *,
    max_gap_days: float = DEFAULT_MAX_GAP_DAYS,
) -> int | None:
    """Market value at the snapshot nearest ``at``, or None if too far away.

    Deliberately NOT `TrainingCorpus.market_value_at`, which takes the most
    recent snapshot at or before ``at``. For a horizon endpoint the nearest
    snapshot may be the following day's, and a backwards-only lookup would
    silently substitute a value up to a day stale in one direction only.

    Returns None rather than 0 when nothing is close enough: a fabricated zero
    would enter the SELECTION term as a full loss of market value.
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT snapshot_at, market_value FROM mv_series WHERE player_id = ? "
            "AND snapshot_at BETWEEN ? AND ? ORDER BY ABS(snapshot_at - ?) LIMIT 1",
            (
                str(player_id),
                at - max_gap_days * SECONDS_PER_DAY,
                at + max_gap_days * SECONDS_PER_DAY,
                at,
            ),
        ).fetchall()
    return int(rows[0][1]) if rows else None


def peak_between(db_path: Path, player_id: str, start: float, end: float) -> int | None:
    """Highest market value over the CLOSED interval ``[start, end]``.

    Feeds the `peak_during_hold - sell_price` sub-measure (REH-33's angle):
    how much of the appreciation we did capture was given back before selling.
    """
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(market_value) FROM mv_series "
            "WHERE player_id = ? AND snapshot_at BETWEEN ? AND ?",
            (str(player_id), float(start), float(end)),
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else None
