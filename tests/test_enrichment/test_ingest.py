"""The budgeted pass: stalest first, stops cleanly, resumes next time."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from rehoboam.enrichment.ingest import IngestBudget, IngestStats, facts_for_ingest, run_ingestion
from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore

LEAGUE = "L"
DAY = date(2026, 9, 14)


def test_facts_for_ingest_maps_stats_into_extra_and_zeroes_errors():
    """A per-player failure (`stats.failed`) is not a run failure -- it rides
    along in `extra` but `errors` stays 0 because the run itself completed."""
    stats = IngestStats(
        universe_size=500,
        status_written=480,
        performance_fetched=120,
        mv_fetched=60,
        failed=3,
        requests=663,
        stopped_by="deadline",
        started_at=1_700_000.0,
        duration_s=210.5,
    )
    facts = facts_for_ingest(stats, app="external", session_id="abc123")
    assert facts.session_id == "abc123"
    assert facts.app == "external"
    assert facts.mode == "ingest"
    assert facts.started_at == 1_700_000.0
    assert facts.duration_s == 210.5
    assert facts.errors == 0
    assert facts.error_text == ""
    assert facts.extra == {
        "universe_size": 500,
        "status_written": 480,
        "performance_fetched": 120,
        "mv_fetched": 60,
        "transfers_fetched": 0,
        "failed": 3,
        "requests": 663,
        "stopped_by": "deadline",
        "started_at": 1_700_000.0,
        "duration_s": 210.5,
        "league": None,
    }


def test_facts_for_ingest_flags_a_run_that_wrote_nothing():
    """Every player failed and none of the write counters moved -- unlike the
    test above, there is no evidence the run did anything, so I7 must not
    read this as a completed ingest."""
    stats = IngestStats(universe_size=5, failed=5, started_at=1_700_000.0, duration_s=1.0)
    facts = facts_for_ingest(stats, app="external", session_id="abc123")
    assert facts.errors == 1
    assert facts.error_text == "5 player(s) failed, nothing written"


def test_facts_for_ingest_any_write_at_all_is_not_an_error():
    """Some players failed, but at least one write landed -- the run made
    progress, so it still counts toward I7 even with failures in `extra`."""
    stats = IngestStats(
        universe_size=5, status_written=1, failed=5, started_at=1_700_000.0, duration_s=1.0
    )
    facts = facts_for_ingest(stats, app="external", session_id="abc123")
    assert facts.errors == 0
    assert facts.error_text == ""


def test_facts_for_ingest_nothing_stale_is_not_an_error():
    """A universe with nothing stale to fetch: every counter is 0, including
    `failed` -- that's a clean no-op run, not a failure."""
    stats = IngestStats(universe_size=5, started_at=1_700_000.0, duration_s=1.0)
    facts = facts_for_ingest(stats, app="external", session_id="abc123")
    assert facts.errors == 0
    assert facts.error_text == ""


def _client(ids: list[str]) -> MagicMock:
    client = MagicMock()
    items = [{"pi": i, "n": i, "pos": 4, "tid": "3", "mv": 1, "ap": 1.0} for i in ids]
    client.get_lineup_selection.side_effect = lambda *, league_id, position, start, max_items: (
        {"it": items} if position == 4 and start == 0 else {"it": []}
    )
    client.get_player_details.return_value = {"st": 0, "prob": 1, "mv": 1, "tid": "3"}
    client.get_competition_player_performance.return_value = {
        "it": [
            {
                "ti": "2025/2026",
                "ph": [{"day": 1, "p": 5, "mp": "90'", "t1": "3", "t2": "4", "pt": "3"}],
            }
        ]
    }
    client.get_player_market_value_history_v2.return_value = {"it": [{"dt": 20000, "mv": 9}]}
    return client


class Clock:
    def __init__(self, t: float = 1_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_full_pass_writes_status_history_and_mv_for_every_player(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a", "b"]), Clock()
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert (
        stats.universe_size,
        stats.status_written,
        stats.performance_fetched,
        stats.mv_fetched,
    ) == (2, 2, 2, 2)
    assert stats.stopped_by is None and stats.failed == 0
    # Universe: positions 1-3 answer one empty page each, position 4 a full page
    # then an empty one = 5 requests; then 3 per player.
    assert stats.requests == 5 + 2 * 3
    assert store.status_on("a", DAY)["lineup_probability"] == 1
    with connect(store_dsn) as conn:
        n = conn.execute("select count(*) as n from rehoboam.player_match_history").fetchone()["n"]
    assert n == 2


def test_fresh_players_are_skipped_and_stale_ones_refreshed_oldest_first(store_dsn):
    store, client, clock = (
        CorpusStore(dsn=store_dsn),
        _client(["a", "b", "c"]),
        Clock(100_000.0),
    )
    store.upsert_players([{"player_id": i, "position": "Forward"} for i in ("a", "b", "c")])
    for pid, at in (("a", 10_000.0), ("b", 99_000.0), ("c", 20_000.0)):
        store.mark_fetched(pid, status=True, performance=True, mv=True)
        with connect(store_dsn) as conn:
            conn.execute(
                "update rehoboam.sweep_progress set status_fetched_at = %s, "
                "performance_fetched_at = %s, mv_fetched_at = %s where player_id = %s",
                (at, at, at, pid),
            )
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.status_written == 2  # b is fresh
    calls = [c.kwargs["player_id"] for c in client.get_player_details.call_args_list]
    assert calls == ["a", "c"]  # oldest first


def test_deadline_stops_cleanly_and_next_run_resumes(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a", "b", "c"]), Clock()
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)

    def _details(*, league_id, player_id):
        clock.t += 300  # each details call burns five minutes
        return {"st": 0, "prob": 2, "mv": 1, "tid": "3"}

    client.get_player_details.side_effect = _details
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.stopped_by == "deadline"
    assert stats.status_written == 2
    assert stats.performance_fetched == 1
    assert stats.mv_fetched == 1
    # Next run: b's remaining kinds, then all of c.
    client.get_player_details.side_effect = None
    budget2 = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats2 = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget2,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats2.stopped_by is None
    assert stats2.status_written == 1  # only c; b's status is fresh
    assert stats2.performance_fetched == 2
    assert stats2.mv_fetched == 2


def test_request_cap_stops_cleanly(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a", "b"]), Clock()
    budget = IngestBudget(deadline=clock.t + 480, max_requests=6, now=clock)  # 5 pages + 1
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.stopped_by == "cap" and stats.requests == 6
    assert stats.status_written == 1
    assert stats.performance_fetched == 0


def test_a_failing_player_is_counted_and_the_pass_continues(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a", "b"]), Clock()
    client.get_player_details.side_effect = [RuntimeError("boom"), {"st": 0, "prob": 1}]
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.failed == 1 and stats.status_written == 1
    assert (
        stats.performance_fetched == 2
    )  # a's performance and mv still run despite the status fail
    assert store.players_needing_fetch("status") == ["a"]  # not marked, retried next run


def test_a_failing_store_write_is_counted_and_leaves_the_player_unmarked(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a"]), Clock()
    store.record_match_history = MagicMock(side_effect=RuntimeError("boom"))
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.failed == 1
    assert stats.performance_fetched == 0  # the write failed, not just the fetch
    assert stats.status_written == 1 and stats.mv_fetched == 1  # the other kinds still ran
    assert stats.stopped_by is None
    assert store.players_needing_fetch("performance") == ["a"]  # not marked, retried next run


def test_mv_refreshes_on_its_own_wider_window(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a"]), Clock()
    store.upsert_players([{"player_id": "a", "position": "Forward"}])
    store.mark_fetched("a", status=True, performance=True, mv=True)
    with connect(store_dsn) as conn:
        conn.execute(
            "update rehoboam.sweep_progress set status_fetched_at = %s, "
            "performance_fetched_at = %s, mv_fetched_at = %s where player_id = 'a'",
            (clock.t - 100_000, clock.t - 100_000, clock.t - 100_000),
        )
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=200_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.status_written == 1
    assert stats.performance_fetched == 1
    assert stats.mv_fetched == 0


def test_status_refreshes_on_its_own_shorter_window(store_dsn):
    """Status is the cheap kind and the one a session needs fresh: a run three
    hours before a session must re-read it even though performance is fresh."""
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a"]), Clock()
    store.upsert_players([{"player_id": "a", "position": "Forward"}])
    store.mark_fetched("a", status=True, performance=True, mv=True)
    with connect(store_dsn) as conn:
        conn.execute(
            "update rehoboam.sweep_progress set status_fetched_at = %s, "
            "performance_fetched_at = %s, mv_fetched_at = %s where player_id = 'a'",
            (clock.t - 50_000, clock.t - 50_000, clock.t - 50_000),  # ~14 h ago
        )
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=200_000,
        status_stale_after_seconds=36_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.status_written == 1
    assert stats.performance_fetched == 0
    assert stats.mv_fetched == 0


def test_facts_for_ingest_carries_the_calibration_outcome():
    from rehoboam.enrichment.ingest import IngestStats, facts_for_ingest

    facts = facts_for_ingest(
        IngestStats(status_written=1),
        app="external",
        session_id="x",
        calibration={"reported": [4], "waiting": {}, "error": None},
    )
    assert facts.extra["calibration"] == {"reported": [4], "waiting": {}, "error": None}
    assert facts.errors == 0


def test_transfers_kind_is_scheduled_and_fetched_once_per_player(store_dsn):
    """`transfers_stale_after_seconds`, when given, adds `transfers` as a stale
    kind for a never-fetched player, and `run_ingestion` fetches + marks it."""
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a"]), Clock()
    client.get_player_transfer_history.return_value = {"it": []}
    store.upsert_players([{"player_id": "a", "position": "Forward"}])
    assert store.players_needing_any_refresh({"transfers": clock.t}) == [("a", ["transfers"])]

    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        mv_stale_after_seconds=72_000,
        transfers_stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.transfers_fetched == 1
    client.get_player_transfer_history.assert_called_once_with(league_id=LEAGUE, player_id="a")
    assert store.players_needing_any_refresh({"transfers": clock.t}) == []
