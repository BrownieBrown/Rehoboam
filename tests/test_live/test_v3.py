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
        "squad": {**dict.fromkeys(CORE, 5000000), "bench": 2_000_000},
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
        squad={**dict.fromkeys(CORE, 5000000), "bench": 2_000_000, "fresh": 3_000_000},
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


# --- Kickbase's debt cap, as the executor's gate holds it ---------------------------

# Every offer Kickbase saw in the two sessions that hit `err 5050
# ThirtyThreePercentRuleExceeded`, in the order placed: (bid, accepted). The
# wallet and team value are the `team_value_history` readings of the session.
SESSION_2026_09_22 = (
    8_767_588,
    132_909_449,
    [
        (24_526_411, True),  # Burger
        (15_507_334, True),  # Querfeld
        (8_752_708, True),  # Itakura
        (11_070_729, False),  # Kosugi
        (11_074_126, False),  # Mensah
        (8_762_614, False),  # Capaldo
        (6_894_694, False),  # Ulrich
        (16_516_650, False),  # Fernández
    ],
)
SESSION_2026_10_04 = (
    -29_173_555,
    150_193_376,
    [
        (6_722_770, True),  # Poreba
        (7_212_762, True),  # Moore: past 33% of worth (team value + wallet), accepted
        (7_436_682, False),  # Massengo
        (4_150_783, True),  # Scally: past 33% of worth, accepted
        (3_511_693, False),  # Van den Bosch
        (10_771_780, False),  # Collins
        (4_181_366, False),  # Gyamerah
    ],
)


def test_the_headroom_reproduces_every_offer_kickbase_accepted_or_refused():
    """The cap is a third of team value with every open offer counted as won. A third
    of worth (`services/budget.max_debt`) refuses Moore and Scally, whom Kickbase took."""
    for cash, team_value, offers in (SESSION_2026_09_22, SESSION_2026_10_04):
        room = v3.Headroom.of(cash=cash, squad_value=team_value, open_bids=0)
        seen = []
        for bid, _ in offers:
            accepted = bid <= room.spendable
            seen.append((bid, accepted))
            if accepted:
                room = room.after_bid(bid)

        assert seen == offers


def test_an_open_offer_from_an_earlier_session_counts_as_won():
    room = v3.Headroom.of(cash=10_000_000, squad_value=100_000_000, open_bids=4_000_000)

    assert room.spendable == 10_000_000 - 4_000_000 + 33_000_000


def test_a_sale_raises_the_headroom_by_two_thirds_of_its_value():
    """Selling moves value out of the team value the cap is a third of."""
    room = v3.Headroom.of(cash=0, squad_value=100_000_000, open_bids=0)

    assert room.after_sale(30_000_000).spendable - room.spendable == 30_000_000 - 9_900_000


# --- the value a v3 bid is judged against --------------------------------------------


def test_a_bid_order_carries_the_value_it_was_priced_on():
    orders = v3.orders(_decision(), _snapshot())

    assert [(o.player_id, o.reference) for o in orders if o.kind == "bid"] == [
        ("star", 5_000_000),
        ("riser", 1_800_000),
    ]


def test_the_gate_judges_a_v3_bid_against_the_value_at_expiry_it_was_priced_on():
    """Adeline, 2026-11-12: v3 bid +5.9% over 5,231,331, his value after the update
    before the listing expires (Kickbase declines a bid below it); on today's 4,813,563
    the same bid read +15.1% and the solid ceiling refused it."""
    assert v3.gate_reference(5_231_331, live_value=4_813_563) == 5_231_331


def test_a_value_the_nightly_forecast_cannot_reach_is_judged_on_the_live_value():
    """The forecast moves at most 0.95 × 30% a night: a base past that is a stale or
    broken value, and the bid is judged as any other buy, on today's value."""
    assert v3.gate_reference(7_000_000, live_value=4_813_563) == 4_813_563  # +45%
    assert v3.gate_reference(3_000_000, live_value=4_813_563) == 4_813_563  # −38%
    assert v3.gate_reference(5_000_000, live_value=0) == 0
