"""CorpusStore writes the corpus tables in the store, idempotently, and reports staleness."""

from __future__ import annotations

from datetime import date

from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore

PERF = {
    "it": [
        {
            "ti": "2025/2026",
            "ph": [{"day": 1, "p": 80, "mp": "90'", "t1": "3", "t2": "4", "pt": "3"}],
        }
    ]
}
MV = {"it": [{"dt": 20000, "mv": 5_000_000}, {"dt": 20001, "mv": 0}]}
TRANSFERS = {"it": [{"u": "9", "unm": "X", "dt": "2026-08-01T10:00:00Z", "trp": 7, "t": 2}]}


def _universe(store: CorpusStore, *ids: str) -> None:
    store.upsert_players(
        [{"player_id": i, "last_name": i, "position": "Forward", "team_id": "3"} for i in ids]
    )


def test_upsert_players_is_idempotent_and_updates(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    store.upsert_players([{"player_id": "p1", "last_name": "New", "position": "Forward"}])
    with connect(store_dsn) as conn:
        rows = conn.execute("select last_name from rehoboam.player_universe").fetchall()
    assert [r["last_name"] for r in rows] == ["New"]


def test_ensure_players_never_overwrites(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    assert store.ensure_players(["p1", "p2"]) == 1
    assert store.positions_for(["p1", "p2"]) == {"p1": "Forward"}
    assert store.players_missing_position(["p1", "p2", "p3"]) == ["p2", "p3"]


def test_match_history_mv_and_transfers_round_trip(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    assert store.record_match_history("p1", "3", PERF) == 1
    assert store.record_match_history("p1", "3", PERF) == 1  # replace, not duplicate
    assert store.record_mv_series("p1", MV) == 1
    assert store.record_player_transfers("p1", TRANSFERS) == 1
    with connect(store_dsn) as conn:
        n = conn.execute("select count(*) as n from rehoboam.player_match_history").fetchone()
        mv = conn.execute("select market_value from rehoboam.mv_series").fetchone()
        tr = conn.execute("select counterparty_id from rehoboam.player_transfers").fetchone()
    assert (n["n"], mv["market_value"], tr["counterparty_id"]) == (1, 5_000_000, "9")


def test_status_daily_is_one_row_per_player_per_day_and_refetch_replaces(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    day = date(2026, 9, 14)
    store.record_status_daily("p1", day, {"st": 0, "prob": 1, "mv": 5, "tid": 3}, 10.0)
    store.record_status_daily("p1", day, {"st": 1, "prob": 5, "mv": 4, "tid": 3}, 20.0)
    row = store.status_on("p1", day)
    assert (
        row["status"],
        row["lineup_probability"],
        row["market_value"],
        row["fetched_at"],
    ) == (
        1,
        5,
        4,
        20.0,
    )
    assert store.status_on("p1", date(2026, 9, 13)) is None


def test_players_needing_refresh_orders_never_fetched_then_oldest(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a", "b", "c", "d")
    store.mark_fetched("a", status=True)  # now
    with connect(store_dsn) as conn:
        conn.execute(
            "update rehoboam.sweep_progress set status_fetched_at = %s where player_id = 'a'",
            (1_000.0,),
        )
    store.mark_fetched("b", status=True)  # fresh
    assert store.players_needing_refresh("status", older_than=5_000.0) == [
        "c",
        "d",
        "a",
    ]
    assert store.players_needing_fetch("status") == ["c", "d"]


def test_clear_performance_fetched_is_scoped(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a", "b")
    store.mark_fetched("a", performance=True)
    store.mark_fetched("b", performance=True)
    assert store.clear_performance_fetched(["a"]) == 1
    assert store.players_needing_fetch("performance") == ["a"]


def test_players_needing_any_refresh_orders_by_the_stalest_kind(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a", "b", "c")
    with connect(store_dsn) as conn:
        # a: status fresh, performance stale (oldest of all); b: everything fresh; c: never fetched
        conn.execute(
            "insert into rehoboam.sweep_progress (player_id, status_fetched_at, "
            "performance_fetched_at, mv_fetched_at) values "
            "('a', 9_000.0, 1_000.0, 9_000.0), ('b', 9_000.0, 9_000.0, 9_000.0)"
        )
    out = store.players_needing_any_refresh(
        {"status": 5_000.0, "performance": 5_000.0, "mv": 5_000.0}
    )
    assert out == [("c", ["status", "performance", "mv"]), ("a", ["performance"])]


def test_session_pins_one_connection_and_each_connection_block_commits_independently(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a", "b")
    with store.session():
        store.mark_fetched("a", status=True)  # its own top-level transaction, commits now
        try:
            with store.connection() as conn:
                conn.execute(
                    "INSERT INTO rehoboam.sweep_progress (player_id, status_fetched_at) "
                    "VALUES ('b', 1.0) "
                    "ON CONFLICT (player_id) DO UPDATE SET status_fetched_at = excluded.status_fetched_at"
                )
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # b's write rolled back with the exception it raised after; a's earlier,
        # already-committed write on the same pinned connection is unaffected.
        assert store.players_needing_fetch("status") == ["b"]
    assert store.players_needing_fetch("status") == ["b"]


def test_players_needing_fetch_works_inside_and_outside_a_session(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a")
    assert store.players_needing_fetch("status") == ["a"]
    with store.session():
        assert store.players_needing_fetch("status") == ["a"]
        store.mark_fetched("a", status=True)
        assert store.players_needing_fetch("status") == []
    assert store.players_needing_fetch("status") == []


def test_players_needing_any_refresh_applies_per_kind_windows(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a")
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.sweep_progress (player_id, status_fetched_at, "
            "performance_fetched_at, mv_fetched_at) values ('a', 1_000.0, 1_000.0, 1_000.0)"
        )
    # MV window is wider: 1_000 is fresh for mv, stale for the other two.
    out = store.players_needing_any_refresh(
        {"status": 5_000.0, "performance": 5_000.0, "mv": 500.0}
    )
    assert out == [("a", ["status", "performance"])]


def test_players_needing_any_refresh_restricts_to_the_given_player_ids(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    # "b" is in the universe (e.g. a departed player from the historical corpus)
    # but not in this run's live squad ids, and must be excluded entirely.
    _universe(store, "a", "b")
    out = store.players_needing_any_refresh({"status": 5_000.0}, player_ids=["a"])
    assert [pid for pid, _ in out] == ["a"]


def test_players_needing_any_refresh_orders_by_the_stalest_stale_kind_only(store_dsn):
    """A fresh kind's own fetched_at must not enter the sort comparison at all --
    only ignoring it when computing `stale_kinds` (already covered above) isn't
    enough if `least()` still folds every kind's raw value into the ORDER BY.

    "x" is stale on status only; its (fresh) mv reading is a large, irrelevant
    number. "y" is stale on mv only; its (fresh) status reading is even larger
    still. x's real (status) staleness value is the smaller of the two real
    values, so x must come first -- and would not, if the fresh mv/status
    readings above were allowed to leak into either player's sort key.
    """
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "x", "y")
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.sweep_progress (player_id, status_fetched_at, mv_fetched_at) "
            "values ('x', 1.0, 150.0), ('y', 999.0, 95.0)"
        )
    out = store.players_needing_any_refresh({"status": 50.0, "mv": 100.0})
    assert [pid for pid, _ in out] == ["x", "y"]
