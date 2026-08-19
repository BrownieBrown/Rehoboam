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

from dataclasses import dataclass


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
