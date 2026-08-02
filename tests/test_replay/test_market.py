import sqlite3

import pytest

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.market import MarketListing, ReplayMarket

DAY = 86400.0


@pytest.fixture
def market(tmp_path):
    c = TrainingCorpus(tmp_path / "c.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("old", 0.0, 1_000_000, 2, "m", "M"),  # 10 days before cutoff
                ("fresh", 8 * DAY, 5_000_000, 2, "m", "M"),  # 2 days before cutoff
                ("edge", 3 * DAY, 3_000_000, 2, "m", "M"),  # exactly 7 days before
                ("future", 11 * DAY, 9_000_000, 2, "m", "M"),  # after cutoff
                ("assigned", 8 * DAY, 0, 0, "m", "M"),  # not a real sale
            ],
        )
    return ReplayMarket(c)


def test_available_before_includes_only_the_trailing_window(market):
    ids = {lst.player_id for lst in market.available_before(10 * DAY)}
    assert ids == {"fresh", "edge"}


def test_available_before_excludes_future_transactions(market):
    assert "future" not in {lst.player_id for lst in market.available_before(10 * DAY)}


def test_available_before_excludes_non_sale_types(market):
    assert "assigned" not in {lst.player_id for lst in market.available_before(10 * DAY)}


def test_listing_carries_the_real_transaction_price(market):
    listing = next(x for x in market.available_before(10 * DAY) if x.player_id == "fresh")
    assert listing == MarketListing(player_id="fresh", price=5_000_000, transfer_at=8 * DAY)


def test_most_recent_price_wins_for_repeat_transactions(tmp_path):
    c = TrainingCorpus(tmp_path / "d.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("p", 8 * DAY, 4_000_000, 2, "m", "M"),
                ("p", 9 * DAY, 6_000_000, 2, "m", "M"),
            ],
        )
    listings = ReplayMarket(c).available_before(10 * DAY)
    assert len(listings) == 1
    assert listings[0].price == 6_000_000
