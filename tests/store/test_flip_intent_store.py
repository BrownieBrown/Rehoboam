"""A flip is a flip from bid to sale: the intent survives the store round trip.

The pending bid is what becomes the purchase record when the auction is won,
so both rows carry the intent, the target and the hold limit.
"""

from __future__ import annotations

from types import SimpleNamespace

from rehoboam.learning.tracker import FlipIntent, LearningTracker


def _player(pid: str = "p1") -> SimpleNamespace:
    return SimpleNamespace(
        id=pid, first_name="Test", last_name="Flip", price=1_000_000, market_value=1_000_000
    )


def test_a_flip_bid_carries_its_intent_into_the_pending_row(learner):
    tracker = LearningTracker(learner)

    tracker.record_bid_placed(
        _player(), 1_050_000, flip=FlipIntent(target_pct=12.0, max_hold_days=5)
    )

    [row] = learner.get_pending_bids()
    assert row["intent"] == "flip"
    assert row["target_pct"] == 12.0
    assert row["max_hold_days"] == 5


def test_a_points_bid_is_marked_as_such(learner):
    tracker = LearningTracker(learner)

    tracker.record_bid_placed(_player(), 1_050_000, tier="strong_upgrade")

    [row] = learner.get_pending_bids()
    assert row["intent"] == "points"
    assert row["target_pct"] is None
    assert row["max_hold_days"] is None


def test_winning_the_auction_copies_the_intent_onto_the_purchase(learner):
    tracker = LearningTracker(learner)
    tracker.record_bid_placed(
        _player(), 1_050_000, flip=FlipIntent(target_pct=12.0, max_hold_days=5)
    )

    tracker.resolve_auctions(squad_ids={"p1"}, active_bid_ids=set())

    purchase = learner.get_tracked_purchase("p1")
    assert purchase["intent"] == "flip"
    assert purchase["target_pct"] == 12.0
    assert purchase["max_hold_days"] == 5
    assert learner.get_tracked_purchases(intent="flip") == {"p1": purchase}


def test_a_cost_basis_recovered_from_the_feed_has_no_intent(learner):
    learner.add_tracked_purchase(
        player_id="p2", player_name="Feed Player", buy_price=1, buy_date=1.0, source="transfer_feed"
    )

    assert learner.get_tracked_purchase("p2")["intent"] is None
    assert learner.get_tracked_purchases(intent="flip") == {}
