"""REH-75: `run_diagnosis` wires `load_round_trips`, the MV lookups, the
branch-ladder mirror and the decomposition into one pass over
`flip_outcomes`. Task 6 runs it exactly once and publishes what it prints, so
a wrong `buy_date + h * SECONDS_PER_DAY` offset or a double-counted censor
would reach the human-facing artifact unchallenged unless it is tested here.
"""

from __future__ import annotations

import sqlite3

from rehoboam.diagnostics.flip_diagnosis import (
    FLOOR_PRICE,
    Decomposition,
    run_diagnosis,
)
from rehoboam.enrichment.corpus import TrainingCorpus

DAY0 = 1_700_000_000.0
HORIZONS = (14, 30)

# Matches rehoboam/bid_learner.py's schema for both tables `run_diagnosis`
# reads from the learner DB.
_LEARNER_SCHEMA = """
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
    );
    CREATE TABLE matchday_lineup_results (
        league_id TEXT NOT NULL,
        day_number INTEGER NOT NULL,
        matchday_date TEXT NOT NULL,
        total_points INTEGER NOT NULL,
        lineup_player_ids TEXT NOT NULL,
        lineup_count INTEGER NOT NULL,
        snapshot_at REAL NOT NULL,
        PRIMARY KEY (league_id, day_number)
    );
"""

# Five round trips, each isolating exactly one thing `run_diagnosis` must get
# right.
# (player_id, name, buy_price, sell_price, buy_date, sell_date, hold_days)
_TRIPS = [
    # Fully decomposed at every horizon -- pins the buy_date + h * 86400
    # offset: mv snapshots sit exactly at +14d and +30d, nowhere else.
    ("p_normal", "Normal", 1_000_000, 1_150_000, DAY0, DAY0 + 40 * 86400, 40),
    # Has mv_buy and an mv at +14d, nothing near +30d: censored at exactly
    # one horizon.
    ("p_censored", "Censored", 800_000, 900_000, DAY0, DAY0 + 40 * 86400, 40),
    # EUR 500k floor trip, no market data at all: must not add to `censored`.
    ("p_floor", "Floor", FLOOR_PRICE, FLOOR_PRICE, DAY0, DAY0 + 40 * 86400, 40),
    # Ordinary (non-floor) buy/sell, no market data at all: fully censored,
    # unlabelled.
    ("p_no_mv", "NoMarketValue", 700_000, 650_000, DAY0, DAY0 + 40 * 86400, 40),
    # A snapshot 0.5 days AFTER buy_date is numerically closer to buy_date
    # than the one 2 days before it. `mv_nearest` would pick the after
    # snapshot (leaking future price into mv_buy); `market_value_at` must
    # not.
    ("p_leak_check", "LeakCheck", 600_000, 700_000, DAY0, DAY0 + 40 * 86400, 40),
]

_MV_SERIES = {
    "p_normal": [
        (DAY0 - 86400, 1_000_000),
        (DAY0 + 14 * 86400, 1_100_000),
        (DAY0 + 30 * 86400, 1_200_000),
    ],
    "p_censored": [
        (DAY0 - 86400, 800_000),
        (DAY0 + 14 * 86400, 850_000),
    ],
    # p_floor and p_no_mv: deliberately no rows at all.
    "p_leak_check": [
        (DAY0 - 2 * 86400, 500_000),
        (DAY0 + 0.5 * 86400, 900_000),
    ],
}


def _dbs(tmp_path):
    learner_db = tmp_path / "bid_learning.db"
    corpus_db = tmp_path / "training_corpus.db"

    with sqlite3.connect(learner_db) as conn:
        conn.executescript(_LEARNER_SCHEMA)
        conn.executemany(
            "INSERT INTO flip_outcomes (player_id, player_name, buy_price, "
            "sell_price, profit, profit_pct, hold_days, buy_date, sell_date) "
            "VALUES (?, ?, ?, ?, 0, 0.0, ?, ?, ?)",
            [
                (pid, name, buy, sell, hold, buy_date, sell_date)
                for pid, name, buy, sell, buy_date, sell_date, hold in _TRIPS
            ],
        )

    # TrainingCorpus creates player_match_history / mv_series / ... on
    # construction -- reuse that instead of hand-rolling the schema.
    TrainingCorpus(corpus_db)
    with sqlite3.connect(corpus_db) as conn:
        for pid, series in _MV_SERIES.items():
            conn.executemany(
                "INSERT INTO mv_series (player_id, snapshot_at, market_value) VALUES (?, ?, ?)",
                [(pid, at, mv) for at, mv in series],
            )

    return learner_db, corpus_db


def test_run_diagnosis_over_a_small_fixture(tmp_path):
    learner_db, corpus_db = _dbs(tmp_path)
    result = run_diagnosis(learner_db, corpus_db, horizons=HORIZONS)

    assert len(result.rows) == 5
    by_player = {r.trip.player_id: r for r in result.rows}

    # A fully-decomposed row: pins the buy_date + h * SECONDS_PER_DAY offset
    # -- if it were off by even one day, mv_nearest would land on a
    # different snapshot (or none) and these values would not match.
    normal = by_player["p_normal"]
    assert normal.mv_buy == 1_000_000
    assert normal.is_floor_trip is False
    assert normal.branch == "no_trend_data"  # <2 pre-buy points -> has_data False
    assert normal.by_horizon[14] == Decomposition(
        selection=100_000, exit_timing=50_000, entry_premium=0
    )
    assert normal.by_horizon[30] == Decomposition(
        selection=200_000, exit_timing=-50_000, entry_premium=0
    )
    assert normal.peak_during_hold == 1_200_000

    # Censored at exactly one horizon -- the other horizon for the same row
    # decomposes normally.
    censored_row = by_player["p_censored"]
    assert censored_row.mv_buy == 800_000
    assert 14 in censored_row.by_horizon
    assert 30 not in censored_row.by_horizon
    assert censored_row.by_horizon[14] == Decomposition(
        selection=50_000, exit_timing=50_000, entry_premium=0
    )

    # Floor trip: detected via buy_price == sell_price == FLOOR_PRICE alone,
    # independent of market data. No market data at all -> mv_buy is None,
    # by_horizon is empty, branch is "no_trend_data" -- but (see the
    # aggregate assertion below) it must NOT add to `censored`.
    floor_row = by_player["p_floor"]
    assert floor_row.is_floor_trip is True
    assert floor_row.mv_buy is None
    assert floor_row.by_horizon == {}

    # No market value at buy at all (non-floor): fully censored, unlabelled.
    no_mv_row = by_player["p_no_mv"]
    assert no_mv_row.is_floor_trip is False
    assert no_mv_row.mv_buy is None
    assert no_mv_row.branch == "no_trend_data"
    assert no_mv_row.expected_appreciation == 0.0
    assert no_mv_row.by_horizon == {}

    # The no-future-leak regression: a snapshot 0.5 days AFTER buy_date is
    # numerically closer than the one 2 days before it, so a nearest-snapshot
    # lookup would wrongly return 900_000. mv_buy is a decision instant and
    # must come from the strictly at-or-before lookup instead.
    leak_row = by_player["p_leak_check"]
    assert leak_row.mv_buy == 500_000

    # Aggregate censoring bookkeeping across the whole run:
    #   14d: p_no_mv (1) + p_leak_check (1) = 2
    #   30d: p_censored (1) + p_no_mv (1) + p_leak_check (1) = 3
    # p_floor contributes to neither despite having no data anywhere --
    # the floor group is reported separately and must not leak into
    # Censored, which lives inside the headline sweep.
    assert result.censored == {14: 2, 30: 3}
