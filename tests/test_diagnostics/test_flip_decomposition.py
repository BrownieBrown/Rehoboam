"""REH-75: the loss decomposition is an identity, not a model.

The three terms cancel to `sell_price - buy_price` by construction. REH-71's
attribution table carried an `other = delta - explained` term, which is where a
wrong model hides; an identity cannot have one. These tests exist to keep it
that way.
"""

from __future__ import annotations

import pytest

from rehoboam.diagnostics.flip_diagnosis import Decomposition, RoundTrip, decompose


def _trip(**kw) -> RoundTrip:
    base = {
        "trip_id": 1,
        "player_id": "p1",
        "player_name": "Tester",
        "buy_price": 1_000_000,
        "sell_price": 1_100_000,
        "buy_date": 1_700_000_000.0,
        "sell_date": 1_700_000_000.0 + 30 * 86400,
        "hold_days": 30,
    }
    base.update(kw)
    return RoundTrip(**base)


@pytest.mark.parametrize(
    ("buy_price", "sell_price", "mv_buy", "mv_h"),
    [
        (1_000_000, 1_100_000, 1_000_000, 1_050_000),  # bought at MV, market rose
        (
            1_117_000,
            1_000_000,
            1_000_000,
            1_200_000,
        ),  # overpaid, market rose, sold anyway
        (900_000, 800_000, 1_000_000, 700_000),  # bought below MV, market fell
        (500_000, 500_000, 500_000, 500_000),  # the EUR 500k floor case
        (2_000_000, 0, 2_000_000, 1_000_000),  # sold for nothing
    ],
)
def test_the_three_terms_sum_to_the_realised_profit(buy_price, sell_price, mv_buy, mv_h):
    trip = _trip(buy_price=buy_price, sell_price=sell_price)
    d = decompose(trip, mv_buy=mv_buy, mv_h=mv_h)
    assert d.total == trip.realised


def test_entry_premium_is_stored_unnegated_and_negated_in_the_total():
    """The sign convention is load-bearing: the pre-registered dominance rule
    compares `-entry_premium` against the other two, so storing it already
    negated would silently flip which mechanism wins."""
    trip = _trip(buy_price=1_100_000, sell_price=1_000_000)
    d = decompose(trip, mv_buy=1_000_000, mv_h=1_000_000)
    assert d.entry_premium == 100_000
    assert d.total == -100_000


def test_decompositions_add_across_trips():
    """Population totals are per-term sums; that only means anything if
    Decomposition adds componentwise."""
    a = Decomposition(selection=10, exit_timing=-4, entry_premium=3)
    b = Decomposition(selection=-2, exit_timing=7, entry_premium=1)
    assert a + b == Decomposition(selection=8, exit_timing=3, entry_premium=4)
    assert (a + b).total == a.total + b.total
