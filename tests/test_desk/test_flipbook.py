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


def _eleven(defender_value=5_000_000):
    return (
        [_p("g", position="Goalkeeper", ep=60.0, value=5_000_000)]
        + [_p(f"d{i}", position="Defender", ep=60.0, value=defender_value) for i in range(4)]
        + [_p(f"m{i}", ep=60.0, value=5_000_000) for i in range(4)]
        + [_p(f"f{i}", position="Forward", ep=60.0, value=5_000_000) for i in range(2)]
    )


def test_the_repayment_sells_the_bench_first_flips_before_core_worst_move_first():
    flips = [_p("f-up", ep=50.0, value=3_000_000), _p("f-down", ep=50.0, value=3_000_000)]
    core = [*_eleven(), _p("filler", ep=10.0, value=2_000_000)]
    moves = {"f-up": 0.05, "f-down": -0.01}

    sales = flipbook.repay(flips, core, cash=-7_000_000, last_moves=moves)

    assert [p.player_id for p in sales] == ["f-down", "f-up", "filler"]


def test_the_repayment_never_breaks_the_eleven_while_the_bench_can_pay():
    # Three dear defenders: the least points per euro, and every legal formation needs them.
    squad = [p for p in _eleven(defender_value=8_000_000) if p.player_id != "d3"]
    squad += [_p("m4", ep=60.0), _p("f2", position="Forward", ep=60.0)]  # twelve: one on the bench

    sales = flipbook.repay([], squad, cash=-1_000_000, last_moves={})

    assert len(sales) == 1
    assert sales[0].position != "Defender"


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


def test_when_slots_bind_the_euros_per_slot_rank_the_entries():
    cheap = _offer("cheap", 0.60, value=1_000_000)  # +0.55m expected after its premium
    big = _offer("big", 0.20, value=10_000_000)  # +1.5m

    by_pct = flipbook.entries([cheap, big], cash=0, slots=1, allowance=20_000_000)
    by_eur = flipbook.entries([cheap, big], cash=0, slots=1, allowance=20_000_000, by_euros=True)

    assert [o.player.player_id for o in by_pct] == ["cheap"]
    assert [o.player.player_id for o in by_eur] == ["big"]


def test_one_sale_that_covers_the_shortfall_beats_two_that_add_up_to_it():
    squad = [*_eleven(), _p("b1", ep=30.0, value=500_000), _p("b2", ep=55.0, value=1_200_000)]

    sales = flipbook.repay([], squad, cash=-1_000_000, last_moves={})

    assert [p.player_id for p in sales] == ["b2"]


def test_debt_on_flips_stays_within_what_the_flips_raise_at_their_p10():
    # Each 4m flip bid at 5% covers 4m x (1 - 10%) = 3.6m of the 4.2m it borrows.
    a = Offer(
        Player("a", "Midfielder", 4_000_000, 0.0, 0.40, -0.10), 0.05, sellable_before_kickoff=True
    )
    b = Offer(
        Player("b", "Midfielder", 4_000_000, 0.0, 0.30, -0.10), 0.05, sellable_before_kickoff=True
    )

    uncovered = flipbook.entries([a, b], cash=600_000, slots=5, allowance=20_000_000)
    covered = flipbook.entries([a, b], cash=600_000, slots=5, allowance=20_000_000, cover=0)

    assert [o.player.player_id for o in uncovered] == ["a", "b"]
    assert [o.player.player_id for o in covered] == ["a"]  # 0.6m + 3.6m covers a, not a and b
