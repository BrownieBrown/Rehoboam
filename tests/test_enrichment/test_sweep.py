"""Tests for rehoboam.enrichment.sweep — the league-wide corpus sweep."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.enrichment.sweep import fetch_universe, run_sweep

LEAGUE_ID = "1933872"


def _paged_client(pages_by_position: dict[int, list[list[dict]]]) -> MagicMock:
    """Mock whose lineup/selection returns successive pages per position.

    ``pages_by_position`` maps a position code to the list of pages it should
    serve, in order. Any position not listed serves a single empty page, which
    is how the real endpoint signals exhaustion.
    """
    client = MagicMock()

    def _selection(*, league_id, position, start, max_items):
        pages = pages_by_position.get(position, [])
        offset = 0
        for page in pages:
            if offset == start:
                return {"it": page}
            offset += len(page)
        return {"it": []}

    client.get_lineup_selection.side_effect = _selection
    client.get_competition_player_performance.return_value = {
        "it": [
            {
                "ti": "2025/2026",
                "ph": [{"day": 1, "p": 80, "mp": "90'", "t1": "3", "t2": "4"}],
            }
        ]
    }
    client.get_player_market_value_history_v2.return_value = {
        "it": [{"dt": 20000, "mv": 5_000_000}]
    }
    # Default: a successful competition-player-details lookup, matching the
    # live response shape. Individual tests override this to exercise the
    # failure/partial paths.
    client.get_competition_player_details.return_value = {
        "i": "?",
        "fn": "Default",
        "ln": "Player",
        "pos": 3,
        "tid": "9",
        "mv": 1_000_000,
        "ap": 30.0,
    }
    return client


def _client(universe_items: list[dict]) -> MagicMock:
    """Single-page-per-position mock: every item served from position 3."""
    return _paged_client({3: [universe_items]})


def test_fetch_universe_unions_all_four_positions(tmp_path):
    client = _paged_client(
        {
            1: [[{"pi": "10", "n": "Neuer", "pos": 1, "tid": "2"}]],
            2: [[{"pi": "20", "n": "Tah", "pos": 2, "tid": "2"}]],
            3: [[{"pi": "30", "n": "Musiala", "pos": 3, "tid": "2"}]],
            4: [[{"pi": "40", "n": "Kane", "pos": 4, "tid": "2"}]],
        }
    )

    rows = fetch_universe(client, LEAGUE_ID, throttle_seconds=0)

    assert {r["player_id"] for r in rows} == {"10", "20", "30", "40"}


def test_fetch_universe_advances_start_by_actual_count_not_requested_max(tmp_path):
    """The pagination trap. The live server caps pages at 50 no matter what
    `max` asks for. If the loop advanced `start` by the requested size, it
    would ask for start=100 after a 50-item page and skip 50 players — while
    still returning HTTP 200 the whole way."""
    page_one = [{"pi": str(i), "n": f"P{i}", "pos": 2, "tid": "2"} for i in range(50)]
    page_two = [{"pi": str(i), "n": f"P{i}", "pos": 2, "tid": "2"} for i in range(50, 60)]
    client = _paged_client({2: [page_one, page_two]})

    rows = fetch_universe(client, LEAGUE_ID, throttle_seconds=0, page_size=100)

    assert len(rows) == 60, "second page was skipped — start advanced by requested max"
    starts = [c.kwargs["start"] for c in client.get_lineup_selection.call_args_list]
    assert 50 in starts, "expected a request at start=50 (actual first-page length)"


def test_fetch_universe_dedupes_players_seen_twice(tmp_path):
    dupe = {"pi": "1", "n": "Musiala", "pos": 3, "tid": "2"}
    client = _paged_client({2: [[dupe]], 3: [[dupe]]})

    rows = fetch_universe(client, LEAGUE_ID, throttle_seconds=0)

    assert len(rows) == 1


def test_fetch_universe_maps_real_field_names(tmp_path):
    client = _paged_client(
        {
            3: [
                [
                    {
                        "pi": "1",
                        "n": "Musiala",
                        "pos": 3,
                        "tid": "2",
                        "mv": 30_000_000,
                        "ap": 120.0,
                    }
                ]
            ]
        }
    )

    row = fetch_universe(client, LEAGUE_ID, throttle_seconds=0)[0]

    assert row["player_id"] == "1"
    assert row["last_name"] == "Musiala"
    assert row["position"] == "Midfielder"
    assert row["team_id"] == "2"
    assert row["market_value"] == 30_000_000
    assert row["average_points"] == 120.0
    # this endpoint carries no first name at all
    assert row["first_name"] is None


def test_sweep_populates_universe_and_history(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _paged_client(
        {
            3: [
                [
                    {
                        "pi": "1",
                        "n": "Musiala",
                        "pos": 3,
                        "tid": "2",
                        "mv": 30_000_000,
                        "ap": 120.0,
                    }
                ]
            ],
            4: [
                [
                    {
                        "pi": "2",
                        "n": "Kane",
                        "pos": 4,
                        "tid": "2",
                        "mv": 40_000_000,
                        "ap": 150.0,
                    }
                ]
            ],
        }
    )

    stats = run_sweep(client, corpus, league_id=LEAGUE_ID, throttle_seconds=0)

    assert stats.universe_size == 2
    assert stats.performance_fetched == 2
    assert stats.mv_fetched == 2
    assert stats.failed == 0
    assert len(corpus.matches_for_player("1")) == 1


def test_sweep_is_resumable(tmp_path):
    """A second run must not refetch players already marked complete."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])

    run_sweep(client, corpus, league_id=LEAGUE_ID, throttle_seconds=0)
    client.get_competition_player_performance.reset_mock()

    stats = run_sweep(client, corpus, league_id=LEAGUE_ID, throttle_seconds=0)

    assert client.get_competition_player_performance.call_count == 0
    assert stats.performance_fetched == 0
    assert stats.skipped == 1


def test_force_refetch_performance_refetches_completed_players(tmp_path):
    """The one-off escape hatch for the is_home/opponent_team_id fix: a
    player already marked complete must be re-fetched when the flag is set,
    and MV resumability must be untouched by it."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])

    run_sweep(client, corpus, league_id=LEAGUE_ID, throttle_seconds=0)
    client.get_competition_player_performance.reset_mock()
    client.get_player_market_value_history_v2.reset_mock()

    stats = run_sweep(
        client,
        corpus,
        league_id=LEAGUE_ID,
        throttle_seconds=0,
        force_refetch_performance=True,
    )

    assert client.get_competition_player_performance.call_count == 1
    assert stats.performance_fetched == 1
    # MV resumability must be untouched -- the second run must not refetch it.
    assert client.get_player_market_value_history_v2.call_count == 0
    assert stats.mv_fetched == 0


def test_sweep_survives_per_player_failure(tmp_path):
    """One bad player must not abort a 450-player sweep."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client(
        [
            {"pi": "1", "n": "P1", "pos": 3, "tid": "2"},
            {"pi": "2", "n": "P2", "pos": 3, "tid": "2"},
        ]
    )
    client.get_competition_player_performance.side_effect = [
        Exception("500 server error"),
        {"it": [{"ti": "2025/2026", "ph": [{"day": 1, "p": 80, "mp": "90'"}]}]},
    ]

    stats = run_sweep(client, corpus, league_id=LEAGUE_ID, throttle_seconds=0)

    assert stats.failed == 1
    assert stats.performance_fetched == 1
    # the failed player must remain pending so a rerun retries it
    assert "1" in corpus.players_needing_fetch("performance")


def test_dry_run_fetches_universe_but_writes_no_history(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])

    stats = run_sweep(client, corpus, league_id=LEAGUE_ID, dry_run=True, throttle_seconds=0)

    assert stats.universe_size == 1
    assert corpus.matches_for_player("1") == []


def test_dry_run_does_not_resolve_positions_for_extra_ids(tmp_path):
    """dry_run must not make the per-player position-lookup call either —
    universe size (including extras) is reported, nothing else runs."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])

    stats = run_sweep(
        client,
        corpus,
        league_id=LEAGUE_ID,
        dry_run=True,
        throttle_seconds=0,
        extra_player_ids=["99"],
    )

    assert stats.universe_size == 2
    assert stats.positions_resolved == 0
    assert stats.positions_unresolved == 0
    client.get_competition_player_details.assert_not_called()


def test_limit_caps_players_processed(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": str(i), "n": f"P{i}", "pos": 3, "tid": "2"} for i in range(10)])

    stats = run_sweep(client, corpus, league_id=LEAGUE_ID, limit=3, throttle_seconds=0)

    assert stats.performance_fetched == 3


def test_extra_player_ids_get_position_and_name_resolved(tmp_path):
    """Departed players recovered from the learning DB aren't in
    /lineup/selection at all, so they need a manual id list. Unlike the
    performance/MV endpoints, get_competition_player_details carries a real
    position and first name — use it to fully resolve the stub row, not
    leave it bare."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])
    client.get_competition_player_details.return_value = {
        "i": "99",
        "fn": "Joel",
        "ln": "Agyekum",
        "pos": 2,
        "tid": "6",
        "mv": 500_000,
        "ap": 12.0,
    }

    stats = run_sweep(
        client,
        corpus,
        league_id=LEAGUE_ID,
        throttle_seconds=0,
        extra_player_ids=["99"],
    )

    assert stats.universe_size == 2  # 1 live + 1 historical
    assert stats.performance_fetched == 2
    assert stats.mv_fetched == 2
    assert stats.positions_resolved == 1
    assert stats.positions_unresolved == 0
    assert len(corpus.matches_for_player("99")) == 1

    with sqlite3.connect(corpus.db_path) as conn:
        row = conn.execute(
            "SELECT first_name, last_name, position, team_id "
            "FROM player_universe WHERE player_id = '99'"
        ).fetchone()
    assert row == ("Joel", "Agyekum", "Defender", "6")


def test_extra_player_ids_unresolvable_position_stays_null_and_is_counted(tmp_path):
    """A 500/404 or a response missing `pos` must not be guessed at — the
    row stays NULL and the failure is counted rather than silently eaten.
    Performance/MV history are independent and still get fetched."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])
    client.get_competition_player_details.side_effect = Exception(
        "500 - {'err': 2, 'errMsg': 'NotFound'}"
    )

    stats = run_sweep(
        client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"]
    )

    assert stats.positions_resolved == 0
    assert stats.positions_unresolved == 1
    assert stats.performance_fetched == 2  # unaffected by the position failure
    assert stats.mv_fetched == 2

    with sqlite3.connect(corpus.db_path) as conn:
        row = conn.execute("SELECT position FROM player_universe WHERE player_id = '99'").fetchone()
    assert row == (None,)


def test_extra_player_ids_response_missing_pos_stays_null_and_is_counted(tmp_path):
    """A 200 response that simply omits `pos` is a different failure mode
    than an exception — more likely in practice than an outright error — and
    must be handled the same way: NULL, counted, not guessed."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])
    client.get_competition_player_details.return_value = {
        "fn": "Joel",
        "ln": "Agyekum",
        "tid": "6",
        # no "pos" key at all
    }

    stats = run_sweep(
        client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"]
    )

    assert stats.positions_resolved == 0
    assert stats.positions_unresolved == 1
    with sqlite3.connect(corpus.db_path) as conn:
        row = conn.execute("SELECT position FROM player_universe WHERE player_id = '99'").fetchone()
    assert row == (None,)


def test_extra_player_ids_response_with_unmapped_pos_code_stays_null_and_is_counted(tmp_path):
    """A `pos` value outside the known 1-4 range (e.g. a new/unknown code)
    must not be silently accepted — `_POSITIONS.get` returns None for it,
    same treatment as a missing `pos`."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])
    client.get_competition_player_details.return_value = {
        "fn": "Joel",
        "ln": "Agyekum",
        "pos": 9,  # not in {1, 2, 3, 4}
        "tid": "6",
    }

    stats = run_sweep(
        client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"]
    )

    assert stats.positions_resolved == 0
    assert stats.positions_unresolved == 1
    with sqlite3.connect(corpus.db_path) as conn:
        row = conn.execute("SELECT position FROM player_universe WHERE player_id = '99'").fetchone()
    assert row == (None,)


def test_extra_player_ids_failed_position_lookup_is_retried_on_rerun(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])
    client.get_competition_player_details.side_effect = Exception("500 error")

    first = run_sweep(
        client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"]
    )
    assert first.positions_unresolved == 1

    client.get_competition_player_details.side_effect = None
    client.get_competition_player_details.return_value = {
        "fn": "Joel",
        "ln": "Agyekum",
        "pos": 2,
        "tid": "6",
    }
    second = run_sweep(
        client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"]
    )

    assert client.get_competition_player_details.call_count == 2  # retried
    assert second.positions_resolved == 1
    assert second.positions_unresolved == 0


def test_extra_player_ids_resolved_position_is_not_refetched_on_rerun(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])

    run_sweep(client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"])
    client.get_competition_player_details.reset_mock()

    second = run_sweep(
        client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"]
    )

    client.get_competition_player_details.assert_not_called()
    assert second.positions_resolved == 0
    assert second.positions_unresolved == 0


def test_extra_player_ids_overlapping_the_live_universe_are_not_double_counted(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])

    stats = run_sweep(
        client,
        corpus,
        league_id=LEAGUE_ID,
        throttle_seconds=0,
        extra_player_ids=["1"],  # already in the live universe
    )

    assert stats.universe_size == 1
    assert stats.performance_fetched == 1
    # already has a real position from the live sweep — never worth a lookup
    client.get_competition_player_details.assert_not_called()


def test_extra_player_ids_do_not_regress_resumability(tmp_path):
    """A rerun with the same extra ids must not refetch either the live or
    the historical players — performance/MV history, or position."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"pi": "1", "n": "P1", "pos": 3, "tid": "2"}])

    run_sweep(client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"])
    client.get_competition_player_performance.reset_mock()
    client.get_competition_player_details.reset_mock()

    stats = run_sweep(
        client, corpus, league_id=LEAGUE_ID, throttle_seconds=0, extra_player_ids=["99"]
    )

    assert client.get_competition_player_performance.call_count == 0
    assert client.get_competition_player_details.call_count == 0
    assert stats.performance_fetched == 0
    assert stats.skipped == 2
