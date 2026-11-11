"""The flip book (M2-17): the leaders' trade, three rules."""

from __future__ import annotations

from rehoboam.desk import flipbook
from rehoboam.desk.model import Offer, Player


def _p(pid, ep=50.0, value=4_000_000, drift=0.0, position="Midfielder"):
    return Player(pid, position, market_value=value, ep_next=ep, drift=drift)


def test_a_flip_is_sold_the_morning_after_its_first_night_without_a_rise():
    flips = [_p("rising"), _p("flat"), _p("falling"), _p("unknown")]
    moves = {"rising": 0.02, "flat": 0.0, "falling": -0.03, "unknown": None}

    assert [p.player_id for p in flipbook.exits(flips, moves)] == ["flat", "falling"]


def test_the_repayment_sells_flips_first_worst_move_first_then_the_least_ep_per_euro():
    flips = [_p("f-up", value=3_000_000), _p("f-down", value=3_000_000)]
    core = [_p("star", ep=120.0), _p("filler", ep=10.0)]
    moves = {"f-up": 0.05, "f-down": -0.01}

    sales = flipbook.repay(flips, core, cash=-7_000_000, last_moves=moves)

    assert [p.player_id for p in sales] == ["f-down", "f-up", "filler"]


def test_no_repayment_when_the_wallet_is_already_covered():
    assert flipbook.repay([_p("f")], [], cash=0, last_moves={"f": -0.1}) == ()


def _offer(pid, drift, premium=0.05, value=4_000_000, sellable=True):
    return Offer(_p(pid, value=value, drift=drift), premium, sellable_before_kickoff=sellable)


def test_entries_take_the_highest_drift_that_clears_its_premium_within_slots_and_allowance():
    offers = [_offer("a", 0.40), _offer("b", 0.30), _offer("c", 0.04), _offer("d", 0.20)]

    bids = flipbook.entries(offers, cash=0, slots=2, allowance=9_000_000)

    assert [o.player.player_id for o in bids] == ["a", "b"]  # c's drift is below its premium


def test_entries_stop_at_the_debt_allowance():
    offers = [_offer("a", 0.40), _offer("b", 0.30)]  # 4.2m each

    bids = flipbook.entries(offers, cash=0, slots=5, allowance=5_000_000)

    assert [o.player.player_id for o in bids] == ["a"]


def test_a_bid_that_cannot_be_sold_before_kickoff_is_not_bought_on_debt():
    late = _offer("late", 0.40, sellable=False)

    assert flipbook.entries([late], cash=0, slots=5, allowance=9_000_000) == ()
    assert [o.player.player_id for o in flipbook.entries([late], 5_000_000, 5, 9_000_000)] == [
        "late"
    ]
