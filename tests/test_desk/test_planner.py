"""The planner (spec v3 §5): one objective, the behaviours the old rules missed."""

from __future__ import annotations

from rehoboam.desk import planner, solvency
from rehoboam.desk.model import Book, Offer, Player
from rehoboam.desk.planner import Limits

MU = 9 / 255_008  # ten matchdays left at 255,008 € per point


def _p(pid, position="Midfielder", ep=50.0, value=5_000_000, drift=0.0):
    return Player(pid, position, market_value=value, ep_next=ep, drift=drift)


def _eleven(ep=60.0):
    return (
        [_p("g", "Goalkeeper", ep)]
        + [_p(f"d{i}", "Defender", ep) for i in range(4)]
        + [_p(f"m{i}", "Midfielder", ep) for i in range(4)]
        + [_p(f"f{i}", "Forward", ep) for i in range(2)]
    )


def test_a_falling_bench_player_is_sold_whatever_he_was_bought_for():
    marino = _p("marino", ep=20.0, value=6_680_000, drift=-0.10)
    book = Book(held=(*_eleven(), marino), cash=0)

    result = planner.plan(book, [], MU)

    assert [p.player_id for p in result.sells] == ["marino"]


def test_a_falling_starter_worth_his_points_is_kept():
    # No sunk cost and no "core" label: he stays because the 120 points he adds
    # this week outweigh a 3% fall. Nothing in his history decides it.
    star = _p("star", "Forward", ep=120.0, value=20_000_000, drift=-0.03)
    squad = [p for p in _eleven() if p.player_id != "f1"] + [star]

    result = planner.plan(Book(held=tuple(squad), cash=0), [], MU)

    assert result.sells == ()


def test_a_rising_listing_is_bought_when_its_drift_beats_the_premium():
    riser = Offer(_p("riser", value=3_000_000, drift=0.40), premium=0.094)

    result = planner.plan(Book(held=tuple(_eleven()), cash=10_000_000), [riser], MU)

    assert [o.player.player_id for o in result.bids] == ["riser"]


def test_a_listing_whose_premium_beats_its_drift_is_not_bought():
    flat = Offer(_p("flat", ep=0.0, value=3_000_000, drift=0.02), premium=0.094)

    result = planner.plan(Book(held=tuple(_eleven()), cash=10_000_000), [flat], MU)

    assert result.bids == ()


def test_a_buy_the_cash_cannot_cover_is_funded_by_a_sale_or_not_made():
    bench = _p("bench", ep=10.0, value=4_000_000, drift=0.0)
    riser = Offer(_p("riser", value=3_000_000, drift=0.40), premium=0.094)
    book = Book(held=(*_eleven(), bench), cash=0)

    result = planner.plan(book, [riser], MU)

    assert [p.player_id for p in result.sells] == ["bench"]
    assert [o.player.player_id for o in result.bids] == ["riser"]
    assert solvency.solvent(book, result.sells, result.bids)


def test_a_starter_is_never_sold_into_a_hole_only_a_won_auction_would_fill():
    # Spec C1: bids count as lost for fielding. Selling a starter to fund a
    # better player is fine only when the squad can still field eleven without him.
    upgrade = Offer(_p("upgrade", ep=150.0, value=3_000_000, drift=0.40), premium=0.05)
    book = Book(held=tuple(_eleven()), cash=0)

    result = planner.plan(book, [upgrade], MU)

    assert result.sells == ()
    assert result.bids == ()


def test_open_bids_count_as_won_for_the_cash():
    # EP 0, so funding him by selling a 60-point starter is never worth it.
    riser = Offer(_p("riser", ep=0.0, value=3_000_000, drift=0.40), premium=0.094)
    book = Book(held=tuple(_eleven()), cash=10_000_000, committed=9_000_000, open_bids=1)

    assert planner.plan(book, [riser], MU).bids == ()


def test_the_trade_caps_hold():
    offers = [Offer(_p(f"r{i}", value=1_000_000, drift=0.40), premium=0.05) for i in range(6)]
    limits = Limits(max_buys=2, max_sales=1)

    result = planner.plan(Book(held=tuple(_eleven()), cash=50_000_000), offers, MU, limits)

    assert len(result.bids) == 2


def test_the_squad_never_exceeds_fifteen():
    offers = [Offer(_p(f"r{i}", value=1_000_000, drift=0.40), premium=0.05) for i in range(6)]

    result = planner.plan(
        Book(held=tuple(_eleven()), cash=50_000_000), offers, MU, Limits(max_buys=6)
    )

    assert 11 + len(result.bids) - len(result.sells) <= 15


def test_an_insolvent_book_sells_the_cheapest_players_first_and_keeps_the_eleven():
    bench = [_p(f"b{i}", ep=5.0, value=4_000_000) for i in range(3)]
    book = Book(held=(*_eleven(), *bench), cash=-7_000_000)

    result = planner.plan(book, [], MU)

    assert {p.player_id for p in result.sells} == {"b0", "b1"}
    assert solvency.cash_after(book, result.sells, ()) >= 0
