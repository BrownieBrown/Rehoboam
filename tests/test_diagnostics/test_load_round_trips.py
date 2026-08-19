"""REH-75: `load_round_trips` maps `flip_outcomes` columns into the
population every other module in this package assumes is correct. It has no
fixture test today, and a column-order or rename regression -- a
`buy_price`/`sell_price` swap, for instance -- would silently invert every
realised value and reach a human-facing evidence document.
"""

from __future__ import annotations

import sqlite3

from rehoboam.diagnostics.flip_diagnosis import load_round_trips

# Matches rehoboam/bid_learner.py's `CREATE TABLE flip_outcomes` exactly.
_SCHEMA = """
    CREATE TABLE flip_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id TEXT NOT NULL,
        player_name TEXT NOT NULL,
        buy_price INTEGER NOT NULL,
        sell_price INTEGER NOT NULL,
        profit INTEGER NOT NULL,
        profit_pct REAL NOT NULL,
        hold_days INTEGER NOT NULL,
        buy_date REAL NOT NULL,
        sell_date REAL NOT NULL,
        trend_at_buy TEXT,
        average_points REAL,
        position TEXT,
        was_injured INTEGER NOT NULL DEFAULT 0
    )
"""

# Two rows with deliberately distinct values in every column `load_round_trips`
# selects, so a positional mis-mapping (buy_price/sell_price swapped, dates
# transposed, ...) fails loudly instead of coincidentally matching. p2 is
# listed (and therefore inserted, and therefore assigned the lower rowid)
# FIRST despite having the LATER buy_date, so the ordering test below can
# only pass if `load_round_trips` actually orders by buy_date -- insertion
# order or rowid order would return them the other way round.
_ROWS = [
    (
        "p2",
        "Player Two",
        2_500_000,
        1_900_000,
        -600_000,
        -24.0,
        45,
        1_705_000_000.0,
        1_708_888_000.0,
        "falling",
        12.0,
        "Defender",
        1,
    ),
    (
        "p1",
        "Player One",
        1_000_000,
        1_200_000,
        200_000,
        20.0,
        10,
        1_700_000_000.0,
        1_700_864_000.0,
        "rising",
        35.0,
        "Midfielder",
        0,
    ),
]


def _db(tmp_path):
    path = tmp_path / "bid_learning.db"
    with sqlite3.connect(path) as conn:
        conn.execute(_SCHEMA)
        conn.executemany(
            "INSERT INTO flip_outcomes (player_id, player_name, buy_price, "
            "sell_price, profit, profit_pct, hold_days, buy_date, sell_date, "
            "trend_at_buy, average_points, position, was_injured) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _ROWS,
        )
    return path


def test_every_selected_column_lands_on_the_right_field(tmp_path):
    db_path = _db(tmp_path)
    trips = load_round_trips(db_path)
    assert len(trips) == 2

    by_player = {t.player_id: t for t in trips}

    p1 = by_player["p1"]
    assert p1.player_name == "Player One"
    assert p1.buy_price == 1_000_000
    assert p1.sell_price == 1_200_000
    assert p1.buy_date == 1_700_000_000.0
    assert p1.sell_date == 1_700_864_000.0
    assert p1.hold_days == 10

    p2 = by_player["p2"]
    assert p2.player_name == "Player Two"
    assert p2.buy_price == 2_500_000
    assert p2.sell_price == 1_900_000
    assert p2.buy_date == 1_705_000_000.0
    assert p2.sell_date == 1_708_888_000.0
    assert p2.hold_days == 45

    # trip_id is the autoincrement id -- confirm it survived the mapping as an
    # int and stayed distinct per row (would collide under a broken mapping).
    assert {p1.trip_id, p2.trip_id} == {1, 2}


def test_rows_are_ordered_by_buy_date_not_by_insertion_order(tmp_path):
    """`load_round_trips` promises "oldest first" (see its docstring). p2 was
    inserted first (rowid 1) but bought later, so this can only pass if the
    ORDER BY clause -- not insertion or rowid order -- decided the result."""
    trips = load_round_trips(_db(tmp_path))
    assert [t.player_id for t in trips] == ["p1", "p2"]
