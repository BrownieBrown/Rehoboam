"""The live adapter (ADR-0008..0013): a snapshot of the real league becomes the
simulator's state, so the live session runs the same `engine.decide`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rehoboam.live import v3
from rehoboam.sim.history import value_day
from tests.test_sim.test_flipbook_engine import CORE, _world

UTC = timezone.utc
NOW = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)


def test_inside_the_nightly_update_the_session_decides_as_of_just_before_it():
    during = datetime(2026, 11, 11, 21, 10, tzinfo=UTC)  # 22:10 Berlin (CET)
    morning = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)

    assert v3.decision_time(during) == datetime(2026, 11, 11, 20, 59, tzinfo=UTC)
    assert v3.decision_time(morning) == morning


def test_only_kickbases_own_listings_are_read_with_their_exact_expiry_and_our_bid():
    items = [
        {
            "i": "riser",
            "mv": 1_800_000,
            "exs": 3600,
            "dt": "2026-10-02T08:30:00Z",
            "uop": 1_900_000,
        },
        {"i": "sold-by-a-manager", "mv": 5_000_000, "exs": 7200, "u": {"i": "m1"}},
        {"i": "no-expiry", "mv": 5_000_000},
    ]

    listings = v3.listings_from_payload(items, NOW)

    assert [(x.player_id, x.expires_at, x.our_bid) for x in listings] == [
        ("riser", NOW + timedelta(hours=1), 1_900_000)
    ]
    assert listings[0].listed_at == datetime(2026, 10, 2, 8, 30, tzinfo=UTC)


def _snapshot(**changes):
    base = {
        "now": NOW,
        "cash": 1_000_000,
        "squad": {**{pid: 5_000_000 for pid in CORE}, "bench": 2_000_000},
        "listings": (
            v3.OpenListing(
                "riser",
                listed_at=datetime(2026, 10, 2, 8, 30, tzinfo=UTC),
                expires_at=datetime(2026, 10, 3, 13, 0, tzinfo=UTC),
                market_value=1_777_836,
            ),
        ),
        "books": {},
    }
    return v3.Snapshot(**{**base, **changes})


def test_the_snapshot_becomes_the_simulators_state():
    snap = _snapshot(
        squad={**{pid: 5_000_000 for pid in CORE}, "bench": 2_000_000, "fresh": 3_000_000},
        listings=(
            v3.OpenListing(
                "riser",
                listed_at=NOW - timedelta(hours=10),
                expires_at=NOW + timedelta(hours=17),
                market_value=1_777_836,
                our_bid=1_900_000,
            ),
        ),
        books={"fresh": "flip", "riser": "flip"},
    )

    world, state = v3.to_world_and_state(_world(), snap, NOW)

    assert state.cash == 1_000_000
    assert state.held == frozenset({*CORE, "bench", "fresh"})
    assert [(p.listing.player_id, p.bid, p.book) for p in state.pending] == [
        ("riser", 1_900_000, "flip")
    ]
    assert [(e.kind, e.player_id, e.book) for e in state.events] == [("won", "fresh", "flip")]
    assert world.paths["fresh"][value_day(NOW)] == 3_000_000  # today's value comes from the API
    assert world.reset_at == v3.backtest.SEASON_END


def test_the_live_decision_is_the_replays_decision():
    world, state = v3.to_world_and_state(_world(), _snapshot(), NOW)

    decision = v3.engine.decide(world, state, NOW, v3.POLICY)

    assert "bench" in {p.player_id for p in decision.sells}  # a core player the eleven does not use
