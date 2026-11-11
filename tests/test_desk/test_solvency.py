"""Solvency with planned debt (spec v3 §5.3 C3/C4, §5.6)."""

from __future__ import annotations

from rehoboam.desk import planner, solvency
from rehoboam.desk.model import Book, Offer, Player

MU = 9 / (10 * 255_008)


def _p(pid, position="Midfielder", ep=60.0, value=5_000_000, drift=0.0, low=-0.10):
    return Player(pid, position, market_value=value, ep_next=ep, drift=drift, drift_low=low)


def _eleven():
    return (
        [_p("g", "Goalkeeper")]
        + [_p(f"d{i}", "Defender") for i in range(4)]
        + [_p(f"m{i}", "Midfielder") for i in range(4)]
        + [_p(f"f{i}", "Forward") for i in range(2)]
    )


BENCH = [_p("b1", ep=5.0, value=4_000_000), _p("b2", ep=5.0, value=4_000_000)]


def _book(cash, last=False, bench=BENCH):
    return Book(held=(*_eleven(), *bench), cash=cash, last_before_kickoff=last)


def test_the_bench_at_its_p10_covers_the_debt():
    # Two bench players at 4m with a -10% p10 cover 7.2m.
    assert solvency.bench_cover([*_eleven(), *BENCH]) == 7_200_000
    assert solvency.solvent(_book(-7_200_000), (), ())
    assert not solvency.solvent(_book(-7_200_001), (), ())


def test_no_debt_survives_the_last_session_before_kickoff():
    assert not solvency.solvent(_book(-1, last=True), (), ())


def test_the_starters_never_cover_a_debt():
    assert not solvency.solvent(_book(-1, bench=[]), (), ())


def test_the_debt_is_capped_at_a_third_of_the_squad_value():
    big_bench = [_p(f"b{i}", ep=1.0, value=20_000_000, low=0.0) for i in range(4)]
    squad_value = 11 * 5_000_000 + 4 * 20_000_000

    assert not solvency.solvent(_book(-round(0.34 * squad_value), bench=big_bench), (), ())
    assert solvency.solvent(_book(-round(0.32 * squad_value), bench=big_bench), (), ())


def test_a_riser_is_bought_on_debt_the_bench_covers():
    riser = Offer(_p("riser", ep=0.0, value=3_000_000, drift=0.40), premium=0.05)

    result = planner.plan(_book(0), [riser], MU)

    assert [o.player.player_id for o in result.bids] == ["riser"]
    assert result.sells == ()


def test_the_last_session_repays_the_debt_by_selling_the_bench_first():
    result = planner.plan(_book(-6_000_000, last=True), [], MU)

    assert {p.player_id for p in result.sells} <= {"b1", "b2"}
    assert solvency.cash_after(_book(-6_000_000, last=True), result.sells, ()) >= 0


# --- M2-13: the trading book covers its own debt -----------------------------


def _flip(sellable=True, value=3_000_000, low=-0.10, premium=0.05):
    player = _p("flip", ep=0.0, value=value, drift=0.30, low=low)
    return Offer(player, premium=premium, sellable_before_kickoff=sellable)


def _eleven_book(cash, open_bid_cover=0):
    return Book(
        held=tuple(_eleven()), cash=cash, open_bid_cover=open_bid_cover, last_before_kickoff=False
    )


def test_a_bid_sold_before_kickoff_covers_its_own_debt_at_its_p10():
    # Bid 3.15m on a 3m flip with a -10% p10: it raises 2.7m, so 450k of cash is needed.
    assert solvency.solvent(_eleven_book(450_000), (), (_flip(),))
    assert not solvency.solvent(_eleven_book(449_999), (), (_flip(),))


def test_a_bid_that_settles_too_late_to_sell_covers_nothing():
    assert not solvency.solvent(_eleven_book(450_000), (), (_flip(sellable=False),))


def test_an_open_bid_keeps_covering_its_debt_in_the_next_session():
    # Last session's bid is now pending: its cost is committed, its cover rides on the book.
    book = Book(
        held=tuple(_eleven()),
        cash=450_000,
        committed=3_150_000,
        open_bids=1,
        open_bid_cover=2_700_000,
        last_before_kickoff=False,
    )

    assert solvency.solvent(book, (), ())
    assert planner.plan(book, [], MU).sells == ()


def test_the_bid_cover_never_survives_the_last_session():
    book = Book(held=tuple(_eleven()), cash=450_000, last_before_kickoff=True)

    assert not solvency.solvent(book, (), (_flip(),))
