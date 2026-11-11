"""Counterfactual auctions (spec v3 §8.2)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from rehoboam.sim import auction
from rehoboam.sim.auction import NO_RIVAL, UNKNOWN, Rival
from rehoboam.sim.history import BUY, SELL, Listing, Transfer

LISTED = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc)  # 22:30 Berlin (CEST)
LISTING = Listing("p1", LISTED, EXPIRES, last_seen=LISTED, market_value=4_000_000)
# A buy an hour after expiry (23:30 Berlin, 10-21) is priced against the point dated 10-21.
PATHS = {"p1": {date(2026, 10, 21): 4_000_000}}


def _buy(manager="rival", at=EXPIRES + timedelta(hours=1), price=4_200_000, kind=BUY):
    return Transfer(manager_id=manager, player_id="p1", at=at, kind=kind, price=price)


def _rival(*transfers, self_ids=frozenset({"us"})):
    return auction.rival_for(LISTING, auction.buys_by_player(transfers), PATHS, self_ids)


def test_a_league_buyer_sets_the_premium_to_beat():
    rival = _rival(_buy(price=4_200_000))

    assert rival.premium == pytest.approx(0.05)
    assert auction.wins(0.05, rival) is False  # equal bids go to the earliest
    assert auction.wins(0.051, rival) is True


def test_no_buyer_means_any_bid_at_market_value_wins():
    rival = _rival()

    assert rival == NO_RIVAL
    assert auction.wins(0.0, rival) is True
    assert auction.wins(-0.01, rival) is False


def test_our_own_real_buy_is_won_at_our_price_and_unknown_below_it():
    rival = _rival(_buy(manager="us", price=4_200_000))

    assert not rival.known
    assert rival.beaten_at == pytest.approx(0.05)
    assert auction.wins(rival.beaten_at, rival) is True  # what we really paid beat the runner-up
    assert auction.wins(0.01, rival) is None
    assert auction.wins(0.5, UNKNOWN) is None


def test_buys_outside_the_listing_window_and_sales_are_ignored():
    rival = _rival(
        _buy(at=LISTED - timedelta(hours=1)),
        _buy(at=EXPIRES + auction.SETTLE_GRACE + timedelta(minutes=1)),
        _buy(kind=SELL),
    )

    assert rival == NO_RIVAL


def test_an_unpriced_buy_is_unknown_not_free():
    late = _buy(at=EXPIRES + timedelta(hours=1))
    rival = auction.rival_for(LISTING, auction.buys_by_player([late]), {"p1": {}}, frozenset())

    assert rival == UNKNOWN
    assert isinstance(rival, Rival)
