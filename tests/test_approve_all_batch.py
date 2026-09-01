"""Approving a session's recommended set in one tap (REH-117).

The overview presents the proposals that fit the budget as one set, so the
callback has to be able to take that set. `approveall:<batch_id>` executes the
batch's still-pending proposals in the order they were recorded, through the
same `execute_proposal` the single-tap path uses — a second execute path would
be the seventh copy of one rule in this repo.

Ordering matters here in a way it does not for a single tap: the set was
chosen to fit the budget as a whole, and each execution re-validates against
live state. If a market value moved and the gate refuses one, the rest still
go — a refusal means "next", not "abandon the set".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.kickbase_client import MarketPlayer

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")
BATCH = "batch0001"


def _settings():
    """An explicit webhook secret — `authorize` rejects an empty one, and
    reading it from the ambient environment made these pass locally off a
    developer `.env` and fail in CI with "Unauthorized."."""
    s = Settings(kickbase_email="test@example.com", kickbase_password="x")
    s.telegram_webhook_secret = "s3cret"
    return s


def _listing(pid, price):
    return MarketPlayer(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position="Midfielder",
        team_id=f"club{pid}",
        team_name="",
        price=price,
        market_value=price,
        points=0,
        average_points=60.0,
        status=0,
    )


class _Api:
    def __init__(self, listings, budget=100_000_000):
        self.listings = list(listings)
        self.budget = budget
        self.bought: list[tuple[str, int]] = []

    def get_market(self, league):
        return list(self.listings)

    def get_squad(self, league):
        return []

    def get_my_bids(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": self.budget, "team_value": 100_000_000}

    def buy_player(self, league, player, price):
        self.bought.append((player.id, price))
        self.budget -= price
        return True


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


def _record(learner, pid, bid, *, batch_id=BATCH):
    learner.record_proposal(
        proposal_id=f"prop{pid}",
        player_id=pid,
        player_name=f"P{pid}",
        bid=bid,
        market_value=bid,
        message="m",
        tier="marginal",
        batch_id=batch_id,
    )


class TestTheBatchIsPersisted:
    def test_a_proposal_remembers_its_batch(self, learner):
        _record(learner, "1", 1_000_000)

        assert learner.get_proposal("prop1")["batch_id"] == BATCH

    def test_pending_proposals_in_a_batch_come_back_in_order(self, learner):
        _record(learner, "1", 1_000_000)
        _record(learner, "2", 2_000_000)
        _record(learner, "3", 3_000_000, batch_id="other")

        rows = learner.pending_in_batch(BATCH)

        assert [r["player_id"] for r in rows] == ["1", "2"]

    def test_a_resolved_proposal_leaves_the_batch(self, learner):
        _record(learner, "1", 1_000_000)
        _record(learner, "2", 2_000_000)
        learner.set_proposal_status("prop1", "rejected")

        assert [r["player_id"] for r in learner.pending_in_batch(BATCH)] == ["2"]

    def test_an_unbatched_proposal_is_never_swept_up(self, learner):
        """Alternatives and ordinary upgrades keep their own single buttons."""
        learner.record_proposal(
            proposal_id="loose",
            player_id="9",
            player_name="Loose",
            bid=1,
            market_value=1,
            message="m",
        )

        assert learner.pending_in_batch(BATCH) == []


class TestApprovingTheWholeSet:
    def _callback(self, batch_id=BATCH):
        return {"callback_query": {"id": "1", "data": f"approveall:{batch_id}"}}

    def test_every_proposal_in_the_batch_is_bought(self, learner):
        from rehoboam.notify.approval import handle_callback

        _record(learner, "1", 1_000_000)
        _record(learner, "2", 2_000_000)
        api = _Api([_listing("1", 1_000_000), _listing("2", 2_000_000)])
        settings = _settings()

        reply = handle_callback(
            self._callback(),
            settings.telegram_webhook_secret,
            settings=settings,
            learner=learner,
            api=api,
            league=LEAGUE,
        )

        assert api.bought == [("1", 1_000_000), ("2", 2_000_000)], reply
        assert learner.get_proposal("prop1")["status"] == "executed"
        assert learner.get_proposal("prop2")["status"] == "executed"

    def test_a_refusal_does_not_abandon_the_rest_of_the_set(self, learner):
        """The set was chosen together; one stale price must not sink it."""
        from rehoboam.notify.approval import handle_callback

        _record(learner, "1", 90_000_000)  # unaffordable against the live budget
        _record(learner, "2", 2_000_000)
        api = _Api([_listing("1", 1_000_000), _listing("2", 2_000_000)], budget=10_000_000)
        settings = _settings()

        handle_callback(
            self._callback(),
            settings.telegram_webhook_secret,
            settings=settings,
            learner=learner,
            api=api,
            league=LEAGUE,
        )

        assert api.bought == [("2", 2_000_000)]
        assert learner.get_proposal("prop1")["status"] == "failed"
        assert learner.get_proposal("prop2")["status"] == "executed"

    def test_a_second_tap_buys_nothing_more(self, learner):
        """Telegram retries callbacks; the claim is the replay guard."""
        from rehoboam.notify.approval import handle_callback

        _record(learner, "1", 1_000_000)
        api = _Api([_listing("1", 1_000_000)])
        settings = _settings()
        args = {"settings": settings, "learner": learner, "api": api, "league": LEAGUE}

        handle_callback(self._callback(), settings.telegram_webhook_secret, **args)
        handle_callback(self._callback(), settings.telegram_webhook_secret, **args)

        assert api.bought == [("1", 1_000_000)]

    def test_an_empty_batch_says_so(self, learner):
        from rehoboam.notify.approval import handle_callback

        settings = _settings()

        reply = handle_callback(
            self._callback("nothing-here"),
            settings.telegram_webhook_secret,
            settings=settings,
            learner=learner,
            api=_Api([]),
            league=LEAGUE,
        )

        assert "nothing" in reply.lower() or "no " in reply.lower()
