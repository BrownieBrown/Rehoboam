"""The import copies every SQLite row once, and a second run copies nothing."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rehoboam.activity_feed_learner import ActivityFeedLearner
from rehoboam.bid_learner import BidLearner
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.store import SCHEMA, connect
from rehoboam.store.import_sqlite import (
    import_all,
    import_cache,
    import_corpus,
    import_learning,
)
from rehoboam.store.migrate import migrate
from rehoboam.value_history import ValueHistoryCache


def _learning_db(tmp_path: Path) -> Path:
    path = tmp_path / "bid_learning.db"
    learner = BidLearner(db_path=path)
    learner.snapshot_predictions(
        [
            {
                "player_id": "p1",
                "league_id": "L",
                "predicted_at": 100.0,
                "predicted_ep": 55.5,
                "position": "Defender",
                "was_in_best_11": True,
                "marginal_ep_gain": None,
            }
        ]
    )
    ActivityFeedLearner(db_path=path)  # creates league_transfers / market_value_snapshots
    with sqlite3.connect(path) as conn:
        conn.execute(
            "insert into pending_bids (player_id, player_name, our_bid, asking_price, "
            "our_overbid_pct, timestamp) values ('p1', 'One', 1100, 1000, 10.0, 1.0)"
        )
        conn.execute(
            "insert into buy_decisions (timestamp, player_id, player_name, decision, reason) "
            "values (1.0, 'p1', 'One', 'skip', 'test'), (2.0, 'p2', 'Two', 'buy', 'test')"
        )
        conn.execute(
            "insert into flip_outcomes (player_id, player_name, buy_price, sell_price, profit, "
            "profit_pct, hold_days, buy_date, sell_date) "
            "values ('p1', 'One', 10, 12, 2, 20.0, 3, 100.0, 400.0)"
        )
        conn.execute(
            "insert into league_rank_history (snapshot_at, league_id, manager_id, day_number, "
            "rank_overall, rank_matchday, total_points, matchday_points, team_value, is_self) "
            "values (100.0, 'L', 'm', 1, 3, 2, 500, 500, 1000000, 1)"
        )
        conn.execute(
            "insert into league_transfers (activity_id, player_id, player_name, transfer_price, "
            "transfer_type, timestamp, processed_at) "
            "values ('a1', 'p1', 'One', 1234, 1, '2026-09-01T10:00:00Z', 1.0)"
        )
        conn.commit()
    return path


def _corpus_db(tmp_path: Path) -> Path:
    path = tmp_path / "training_corpus.db"
    TrainingCorpus(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "insert into player_universe (player_id, first_name, last_name, position, team_id, "
            "market_value, average_points) "
            "values ('p1', 'O', 'One', 'Defender', 't', 1000000, 50.0)"
        )
        conn.execute(
            "insert into player_match_history (player_id, season, day_number, match_date, points, "
            "minutes, team_id, opponent_team_id, is_home, status) "
            "values ('p1', '2025/2026', 1, '2025-08-23T13:30:00Z', 88, 90, 't', 'u', 1, 5)"
        )
        conn.execute(
            "insert into mv_series (player_id, snapshot_at, market_value) values ('p1', 1.0, 5)"
        )
        conn.commit()
    return path


def _cache_db(tmp_path: Path) -> Path:
    path = tmp_path / "player_history.db"
    cache = ValueHistoryCache(db_path=path)
    cache.cache_performance("p1", "L", {"it": [{"ti": "2025/2026", "ph": []}]})
    cache.cache_history("p1", "L", 365, {"it": [{"dt": 1, "mv": 5}]})
    return path


def test_learning_import_copies_every_row_and_is_idempotent(store_dsn, tmp_path):
    src = _learning_db(tmp_path)
    with connect(store_dsn) as conn:
        migrate(conn)
        report = {r.table: r for r in import_learning(conn, src)}
        assert report["pending_bids"].sqlite_rows == 1
        assert report["pending_bids"].postgres_rows == 1
        assert report["buy_decisions"].postgres_rows == 2
        assert report["flip_outcomes"].postgres_rows == 1
        assert report["league_transfers"].postgres_rows == 1
        assert report["predicted_eps"].postgres_rows == 1
        assert report["league_rank_history"].postgres_rows == 1
        again = {r.table: r for r in import_learning(conn, src)}
        assert again["buy_decisions"].postgres_rows == 2
        assert again["flip_outcomes"].postgres_rows == 1
        conn.execute(
            f"insert into {SCHEMA}.buy_decisions (timestamp, player_id, decision, reason) "
            "values (3.0, 'p3', 'buy', 'after import')"
        )
        ids = [r["id"] for r in conn.execute(f"select id from {SCHEMA}.buy_decisions order by id")]
    assert ids == [1, 2, 3]


def test_corpus_import_copies_every_table(store_dsn, tmp_path):
    src = _corpus_db(tmp_path)
    with connect(store_dsn) as conn:
        migrate(conn)
        report = {r.table: r for r in import_corpus(conn, src)}
        assert report["player_universe"].postgres_rows == 1
        assert report["player_match_history"].postgres_rows == 1
        assert report["mv_series"].postgres_rows == 1
        status = conn.execute(
            f"select status from {SCHEMA}.player_match_history where player_id = 'p1'"
        ).fetchone()["status"]
    assert status == 5


def test_cache_import_folds_both_tables_into_api_cache_as_jsonb(store_dsn, tmp_path):
    src = _cache_db(tmp_path)
    with connect(store_dsn) as conn:
        migrate(conn)
        report = {r.table: r for r in import_cache(conn, src)}
        assert report["api_cache"].postgres_rows == 2
        rows = conn.execute(
            f"select kind, key, payload from {SCHEMA}.api_cache order by kind"
        ).fetchall()
    assert [(r["kind"], r["key"]) for r in rows] == [("mv", "365"), ("performance", "")]
    assert rows[1]["payload"]["it"][0]["ti"] == "2025/2026"


def test_import_all_skips_missing_files(store_dsn, tmp_path):
    with connect(store_dsn) as conn:
        migrate(conn)
        report = import_all(conn, learning=tmp_path / "missing.db", corpus=None, cache=None)
    assert report and all(r.sqlite_rows == -1 for r in report)
