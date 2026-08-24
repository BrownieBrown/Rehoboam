"""Trade proposals persist in bid_learning.db, not a JSON file.

pending_bids.json and tracked_purchases.json were migrated into tables because
loose JSON is not synced to blob storage. A proposal is created by the timer
run and approved by a separate HTTP invocation, so it MUST survive the round
trip or approving does nothing.
"""

import time

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bids.db")


def _record(learner, pid="p1"):
    learner.record_proposal(
        proposal_id=pid,
        player_id="6080",
        player_name="Pavlović",
        bid=32_608_485,
        market_value=32_285_629,
        message="BUY Pavlović",
    )


class TestRoundTrip:
    def test_a_recorded_proposal_can_be_read_back(self, learner):
        _record(learner)
        p = learner.get_proposal("p1")
        assert p is not None
        assert p["player_id"] == "6080"
        assert p["bid"] == 32_608_485
        assert p["status"] == "pending"

    def test_an_unknown_id_returns_none(self, learner):
        assert learner.get_proposal("nope") is None

    def test_created_at_is_populated(self, learner):
        _record(learner)
        assert learner.get_proposal("p1")["created_at"] <= time.time()


class TestStatusTransitions:
    def test_marking_changes_the_status(self, learner):
        _record(learner)
        assert learner.mark_proposal("p1", "approved") is True
        assert learner.get_proposal("p1")["status"] == "approved"

    def test_marking_an_unknown_proposal_returns_false(self, learner):
        assert learner.mark_proposal("nope", "approved") is False


class TestIdempotency:
    def test_a_proposal_can_only_leave_pending_once(self, learner):
        """Telegram retries callbacks. A second tap must not re-approve."""
        _record(learner)
        assert learner.mark_proposal("p1", "approved") is True
        assert learner.mark_proposal("p1", "approved") is False
        assert learner.get_proposal("p1")["status"] == "approved"

    def test_a_rejected_proposal_cannot_later_be_approved(self, learner):
        _record(learner)
        learner.mark_proposal("p1", "rejected")
        assert learner.mark_proposal("p1", "approved") is False
        assert learner.get_proposal("p1")["status"] == "rejected"


class TestUnguardedTransition:
    def test_set_status_works_after_a_proposal_has_been_claimed(self, learner):
        """The webhook claims first, then reports the outcome."""
        _record(learner)
        learner.mark_proposal("p1", "approved")
        learner.set_proposal_status("p1", "executed")
        assert learner.get_proposal("p1")["status"] == "executed"


class TestListing:
    def test_pending_lists_only_pending(self, learner):
        _record(learner, "p1")
        _record(learner, "p2")
        learner.mark_proposal("p2", "approved")
        assert [p["proposal_id"] for p in learner.pending_proposals()] == ["p1"]
