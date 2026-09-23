"""The readers behind `derive-ceilings` and `transfer-study`, on the seeded views."""

from __future__ import annotations

import pytest

from rehoboam.store import connect
from rehoboam.store.transfer_study import (
    our_bids_since,
    outcomes_since,
    study_by_band,
    study_by_manager,
    winners_since,
)
from tests.store.test_transfer_premiums import _seed


def test_winners_exclude_our_own_buys_and_unpriced_ones(store_dsn):
    _seed(store_dsn)
    with connect(store_dsn) as conn:
        winners = winners_since(conn, 0)
    # Rival's two priced buys of p1; our unpriced p2 and our own p3 are out.
    assert sorted(w.premium_pct for w in winners) == pytest.approx([-8.33, 10.0], abs=0.01)


def test_our_bids_come_from_the_auction_ledger(store_dsn):
    _seed(store_dsn)
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.auction_outcomes (player_id, player_name, our_bid, asking_price, "
            "our_overbid_pct, won, timestamp, market_value) values "
            "('x', 'X', 11000000, 10000000, 10.0, 1, 1, 10000000), "
            "('y', 'Y', 5500000, 5000000, 10.0, 0, 1, null)"
        )
        ours = our_bids_since(conn, 0)
    assert [(b.market_value, b.won) for b in ours] == [(10_000_000, True), (5_000_000, False)]


def test_the_band_study_groups_by_season_and_band(store_dsn):
    _seed(store_dsn)
    with connect(store_dsn) as conn:
        rows = study_by_band(conn, bands=(0, 5_000_000, 15_000_000))
    this = [r for r in rows if r["season"] == "2026/2027"]
    priced = next(r for r in this if r["band"] == 5_000_000)
    assert priced["buys"] == 2  # both p1 buys at 10m/12m
    assert priced["resold"] == 2
    assert priced["resold_at_profit"] == 2
    assert float(priced["points_p50"]) == pytest.approx(30.0)
    unpriced = next(r for r in this if r["band"] == -1)
    assert unpriced["buys"] == 1  # our p2, no series
    last = [r for r in rows if r["season"] == "2025/2026"]
    assert len(last) == 1 and last[0]["band"] == 5_000_000


def test_the_manager_study_ranks_by_realised_profit(store_dsn):
    _seed(store_dsn)
    with connect(store_dsn) as conn:
        rows = study_by_manager(conn, season="2026/2027")
    assert [r["manager"] for r in rows] == ["Rival", "Brownie"]
    rival = rows[0]
    assert rival["buys"] == 2
    assert rival["realised_profit"] == 4_000_000  # both buys map to the one sale
    assert rows[1]["is_self"] is True


def test_outcomes_carry_the_resale_as_a_share_of_price(store_dsn):
    _seed(store_dsn)
    with connect(store_dsn) as conn:
        outcomes = outcomes_since(conn, 0)
    # The rival's two p1 buys at 11m, both resold at 13m: +18.18% of price.
    assert len(outcomes) == 2
    assert sorted(o.premium_pct for o in outcomes) == pytest.approx([-8.33, 10.0], abs=0.01)
    assert all(o.profit_pct == pytest.approx(18.18, abs=0.01) for o in outcomes)
