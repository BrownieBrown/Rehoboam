"""ValueHistoryCache over rehoboam.api_cache: the TTL and the replace-on-refetch semantics."""

from __future__ import annotations

from rehoboam.store import connect
from rehoboam.value_history import ValueHistoryCache


def test_performance_round_trip_and_ttl(store_dsn):
    cache = ValueHistoryCache(dsn=store_dsn)
    assert cache.get_cached_performance("p1", "L") is None
    cache.cache_performance("p1", "L", {"it": [{"ti": "2026/2027"}]})
    assert cache.get_cached_performance("p1", "L")["it"][0]["ti"] == "2026/2027"
    assert cache.get_cached_performance("p1", "L", max_age_hours=0) is None


def test_history_is_keyed_by_timeframe_and_refetch_replaces(store_dsn):
    cache = ValueHistoryCache(dsn=store_dsn)
    cache.cache_history("p1", "L", 30, {"it": [1]})
    cache.cache_history("p1", "L", 365, {"it": [2]})
    cache.cache_history("p1", "L", 30, {"it": [3]})
    assert cache.get_cached_history("p1", "L", timeframe=30)["it"] == [3]
    assert cache.get_cached_history("p1", "L", timeframe=365)["it"] == [2]
    with connect(store_dsn) as conn:
        rows = conn.execute(
            "select kind, key from rehoboam.api_cache where player_id = 'p1' order by key"
        ).fetchall()
    assert [(r["kind"], r["key"]) for r in rows] == [("mv", "30"), ("mv", "365")]


def test_cleanup_deletes_old_rows_of_both_kinds(store_dsn):
    cache = ValueHistoryCache(dsn=store_dsn)
    cache.cache_history("p1", "L", 30, {"it": []})
    cache.cache_performance("p1", "L", {"it": []})
    assert cache.cleanup_old_cache(days_to_keep=0) == 2
