"""REH-75: market-value lookups, and what happens when the data is not there.

Censoring is explicit everywhere in this module. A missing snapshot returns
None so the caller must decide; returning 0 would put a fabricated -mv_buy into
the SELECTION term and quietly move the diagnosis.
"""

from __future__ import annotations

import sqlite3

import pytest

from rehoboam.diagnostics.flip_diagnosis import (
    SECONDS_PER_DAY,
    mv_nearest,
    peak_between,
)

DAY0 = 1_700_000_000.0


@pytest.fixture
def corpus_db(tmp_path):
    """A minimal `mv_series`, matching `enrichment/corpus.py`'s schema."""
    path = tmp_path / "corpus.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE mv_series ("
            "player_id TEXT NOT NULL, snapshot_at REAL NOT NULL, "
            "market_value INTEGER NOT NULL, PRIMARY KEY (player_id, snapshot_at))"
        )
    return path


def _insert(path, player_id, series):
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO mv_series (player_id, snapshot_at, market_value) VALUES (?, ?, ?)",
            [(player_id, at, mv) for at, mv in series],
        )


def test_nearest_snapshot_wins_even_when_it_is_after_the_target(corpus_db):
    """Daily snapshots straddle a target instant. `corpus.market_value_at`
    always looks backwards; here the closer snapshot may be the later one."""
    _insert(corpus_db, "p1", [(DAY0, 1_000_000), (DAY0 + SECONDS_PER_DAY, 1_200_000)])
    target = DAY0 + 0.8 * SECONDS_PER_DAY
    assert mv_nearest(corpus_db, "p1", target) == 1_200_000


def test_the_earlier_snapshot_wins_when_it_is_closer(corpus_db):
    _insert(corpus_db, "p1", [(DAY0, 1_000_000), (DAY0 + SECONDS_PER_DAY, 1_200_000)])
    target = DAY0 + 0.2 * SECONDS_PER_DAY
    assert mv_nearest(corpus_db, "p1", target) == 1_000_000


def test_a_snapshot_beyond_the_gap_limit_is_censored_not_used(corpus_db):
    _insert(corpus_db, "p1", [(DAY0, 1_000_000)])
    target = DAY0 + 10 * SECONDS_PER_DAY
    assert mv_nearest(corpus_db, "p1", target, max_gap_days=3.0) is None


def test_an_unknown_player_is_censored(corpus_db):
    assert mv_nearest(corpus_db, "nobody", DAY0) is None


def test_peak_between_includes_both_endpoints(corpus_db):
    """The hold window is closed: a player bought at their peak and sold at it
    must not report a higher peak than either endpoint."""
    _insert(
        corpus_db,
        "p1",
        [
            (DAY0, 900_000),
            (DAY0 + SECONDS_PER_DAY, 1_500_000),
            (DAY0 + 2 * SECONDS_PER_DAY, 1_100_000),
        ],
    )
    assert peak_between(corpus_db, "p1", DAY0, DAY0 + 2 * SECONDS_PER_DAY) == 1_500_000
    assert peak_between(corpus_db, "p1", DAY0, DAY0) == 900_000


def test_peak_between_is_censored_when_the_window_holds_no_snapshot(corpus_db):
    _insert(corpus_db, "p1", [(DAY0, 900_000)])
    assert (
        peak_between(corpus_db, "p1", DAY0 + 5 * SECONDS_PER_DAY, DAY0 + 6 * SECONDS_PER_DAY)
        is None
    )
