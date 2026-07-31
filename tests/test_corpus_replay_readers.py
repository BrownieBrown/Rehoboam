import sqlite3

import pytest

from rehoboam.enrichment.corpus import TrainingCorpus


@pytest.fixture
def corpus(tmp_path):
    c = TrainingCorpus(tmp_path / "c.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("1", 100.0, 5_000_000, 2, "m1", "Alice"),
                ("2", 200.0, 7_000_000, 2, "m2", "Bob"),
                ("3", 300.0, 0, 0, "m1", "Alice"),
                ("4", 250.0, 9_000_000, 2, None, None),
            ],
        )
        conn.executemany(
            "INSERT INTO mv_series (player_id, snapshot_at, market_value) VALUES (?,?,?)",
            [("1", 50.0, 4_000_000), ("1", 150.0, 6_000_000), ("2", 50.0, 8_000_000)],
        )
        conn.executemany(
            "INSERT INTO player_universe (player_id, position, team_id) VALUES (?,?,?)",
            [("1", "Forward", "40"), ("2", "Defender", "7")],
        )
    return c


def test_transfers_between_filters_by_type_and_window(corpus):
    rows = corpus.transfers_between(150.0, 260.0)
    assert [r["player_id"] for r in rows] == ["2", "4"]


def test_transfers_between_excludes_assignments(corpus):
    assert all(r["transfer_type"] == 2 for r in corpus.transfers_between(0.0, 1000.0))


def test_market_value_at_takes_latest_before_cutoff(corpus):
    assert corpus.market_value_at("1", 149.0) == 4_000_000
    assert corpus.market_value_at("1", 151.0) == 6_000_000


def test_market_value_at_returns_none_when_no_history_before_cutoff(corpus):
    assert corpus.market_value_at("1", 10.0) is None
    assert corpus.market_value_at("999", 1000.0) is None


def test_team_ids_for_returns_mapping(corpus):
    assert corpus.team_ids_for(["1", "2", "999"]) == {"1": "40", "2": "7"}
