"""ActivityFeedLearner on the store: dedupe by activity id, and the windowed readers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rehoboam.activity_feed_learner import ActivityFeedLearner


def _feed(activity_id: str, *, when: str, price: int = 5_000_000, buyer: str = "Rival") -> dict:
    return {
        "af": [
            {
                "i": activity_id,
                "t": 15,
                "dt": when,
                "data": {
                    "pi": "p1",
                    "pn": "One",
                    "byr": buyer,
                    "slr": "Kickbase",
                    "trp": price,
                    "t": 1,
                },
            }
        ]
    }


def test_transfers_dedupe_by_activity_id(store_dsn):
    learner = ActivityFeedLearner(dsn=store_dsn)
    first = learner.process_activity_feed(_feed("a1", when="2026-09-01T10:00:00Z"))
    second = learner.process_activity_feed(_feed("a1", when="2026-09-01T10:00:00Z"))
    assert (first["transfers_new"], second["transfers_duplicate"]) == (1, 1)
    stats = learner.get_competitive_bidding_stats("p1")
    assert stats["total_transfers"] == 1 and stats["avg_transfer_price"] == 5_000_000


def test_demand_score_counts_only_the_last_30_days(store_dsn):
    learner = ActivityFeedLearner(dsn=store_dsn)
    recent = (datetime.now(tz=timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (datetime.now(tz=timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    learner.process_activity_feed(_feed("a1", when=recent))
    learner.process_activity_feed(_feed("a2", when=old))
    assert learner.get_player_demand_score("p1") == 60.0


def test_top_competitors_threat_score_is_a_float(store_dsn):
    learner = ActivityFeedLearner(dsn=store_dsn)
    for i in range(3):
        learner.process_activity_feed(
            _feed(f"a{i}", when="2026-09-01T10:00:00Z", price=20_000_000, buyer="Whale")
        )
    top = learner.get_top_competitors(limit=1)[0]
    assert top["name"] == "Whale" and isinstance(top["threat_score"], float)
    assert learner.has_aggressive_competitors() is False  # 3*10 + 20 = 50, not > 100
