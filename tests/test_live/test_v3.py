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


# --- executing a decision (TRADING_MODE=v3) --------------------------------------


def _decision(repaying=False):
    from rehoboam.desk.model import Offer, Player
    from rehoboam.sim.engine import Decision
    from rehoboam.sim.history import Listing

    flip = Player("riser", "Forward", 1_800_000, 20.0, 0.36)
    star = Player("star", "Midfielder", 5_000_000, 90.0, 0.05)
    bench = Player("bench", "Midfielder", 2_000_000, 5.0, 0.0)
    turned = Player("fresh", "Forward", 3_000_000, 30.0, -0.02)
    lst = Listing("x", NOW, NOW + timedelta(hours=10), NOW, 0)
    return Decision(
        sells=(bench, turned),
        bids=(Offer(star, 0.057), Offer(flip, 0.099)),
        books={"star": "core", "riser": "flip"},
        listings={"star": lst, "riser": lst},
        repaying=repaying,
    )


def test_orders_sell_first_and_tag_each_sale_with_its_exit_rule():
    from rehoboam.services.flip_exit import exit_rule_tag

    snap = _snapshot(books={"fresh": "flip"})

    orders = v3.orders(_decision(), snap)

    assert [(o.kind, o.player_id) for o in orders] == [
        ("sell", "bench"),
        ("sell", "fresh"),
        ("bid", "star"),
        ("bid", "riser"),
    ]
    assert [exit_rule_tag(o.reason) for o in orders[:2]] == ["core", "turn"]
    assert [(o.book, o.amount) for o in orders[2:]] == [("core", 5_285_000), ("flip", 1_978_200)]


def test_a_repayment_is_tagged_as_debt_recovery():
    from rehoboam.services.flip_exit import exit_rule_tag

    orders = v3.orders(_decision(repaying=True), _snapshot())

    assert {exit_rule_tag(o.reason) for o in orders if o.kind == "sell"} == {"debt_recovery"}


def test_one_failed_order_does_not_stop_the_others():
    done = []

    def sell(order):
        done.append(order.player_id)
        return True

    def bid(order):
        if order.player_id == "star":
            raise RuntimeError("refused by the gate")
        done.append(order.player_id)
        return True

    results = v3.execute(v3.orders(_decision(), _snapshot()), sell=sell, bid=bid)

    assert done == ["bench", "fresh", "riser"]
    assert [(r.order.player_id, r.ok) for r in results] == [
        ("bench", True),
        ("fresh", True),
        ("star", False),
        ("riser", True),
    ]
    assert "refused by the gate" in results[2].error
