"""The approval webhook is a public endpoint that spends money.

These are adversarial tests, not happy-path ones: forged callbacks, replayed
callbacks, and stale proposals are the failure modes that cost real money.
"""

from unittest.mock import MagicMock, patch

import pytest

from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.notify.approval import authorize, build_callback_response, handle_callback


@pytest.fixture
def learner(tmp_path):
    lr = BidLearner(db_path=tmp_path / "bids.db")
    lr.record_proposal(
        proposal_id="p1",
        player_id="6080",
        player_name="Pavlović",
        bid=32_608_485,
        market_value=32_285_629,
        message="BUY",
    )
    return lr


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "t@e.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "t")
    s = Settings()
    s.telegram_webhook_secret = "s3cret"
    s.max_overbid_pct = 8.0
    return s


@pytest.fixture
def api():
    a = MagicMock()
    a.get_market.return_value = [
        MagicMock(id="6080", market_value=32_285_629, last_name="Pavlović")
    ]
    a.get_squad.return_value = [MagicMock(id=f"s{i}") for i in range(11)]
    a.get_my_bids.return_value = []
    a.get_team_info.return_value = {"budget": 95_317_114}
    return a


def _cb(action="approve", pid="p1"):
    return {"callback_query": {"id": "cb1", "data": f"{action}:{pid}"}}


class TestAuthentication:
    def test_a_missing_secret_is_rejected(self, learner, settings, api):
        out = handle_callback(
            _cb(), None, settings=settings, learner=learner, api=api, league=MagicMock()
        )
        assert "unauthor" in out.lower()
        api.buy_player.assert_not_called()

    def test_a_wrong_secret_is_rejected(self, learner, settings, api):
        out = handle_callback(
            _cb(),
            "wrong",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert "unauthor" in out.lower()
        api.buy_player.assert_not_called()

    def test_the_proposal_is_untouched_after_a_forged_callback(self, learner, settings, api):
        handle_callback(
            _cb(),
            "wrong",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert learner.get_proposal("p1")["status"] == "pending"


class TestApproval:
    def test_a_valid_approval_buys(self, learner, settings, api):
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_called_once()

    def test_a_valid_approval_marks_the_proposal(self, learner, settings, api):
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert learner.get_proposal("p1")["status"] == "executed"


class TestReplay:
    def test_a_second_identical_callback_does_not_buy_twice(self, learner, settings, api):
        """Telegram retries callbacks. Buying twice is real money."""
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert api.buy_player.call_count == 1

    def test_a_failure_after_claiming_marks_the_proposal_failed(self, learner, settings, api):
        """A stranded 'approved' row would be retried and bought twice."""
        api.get_market.side_effect = OSError("kickbase down")
        out = handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()
        assert learner.get_proposal("p1")["status"] == "failed"
        assert "failed" in out.lower()


class TestRejection:
    def test_reject_marks_and_does_not_buy(self, learner, settings, api):
        handle_callback(
            _cb("reject"),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()
        assert learner.get_proposal("p1")["status"] == "rejected"


class TestRevalidation:
    def test_a_player_no_longer_on_the_market_is_not_bought(self, learner, settings, api):
        api.get_market.return_value = []
        out = handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()
        assert "no longer" in out.lower() or "unknown player" in out.lower()

    def test_a_price_that_moved_past_the_cap_is_not_bought(self, learner, settings, api):
        """Market values update daily after 10:00 — a proposal's price is stale."""
        api.get_market.return_value = [
            MagicMock(id="6080", market_value=20_000_000, last_name="Pavlović")
        ]
        handle_callback(
            _cb(),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()

    def test_an_unknown_proposal_id_is_reported_not_executed(self, learner, settings, api):
        out = handle_callback(
            _cb(pid="nope"),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        api.buy_player.assert_not_called()
        assert "not found" in out.lower()


class TestCallbackResponse:
    def test_it_names_the_method_telegram_needs(self):
        out = build_callback_response(_cb(), "Bought Pavlović.")
        assert out["method"] == "answerCallbackQuery"
        assert out["callback_query_id"] == "cb1"

    def test_it_truncates_to_telegrams_limit(self):
        out = build_callback_response(_cb(), "x" * 500)
        assert len(out["text"]) == 200


class TestAuthorize:
    """`authorize` is called by the HTTP trigger before any expensive work."""

    def test_the_right_secret_passes(self):
        assert authorize("s3cret", "s3cret") is True

    def test_a_wrong_secret_fails(self):
        assert authorize("nope", "s3cret") is False

    def test_a_missing_header_fails(self):
        assert authorize(None, "s3cret") is False

    def test_an_unconfigured_secret_rejects_everything(self):
        """Fail closed: no configured secret must not mean 'let anyone in'."""
        assert authorize("anything", "") is False
        assert authorize(None, "") is False


class TestOpenOffersAreDeductedFromBudget:
    """Kickbase reports a budget that ignores open offers.

    `buy_player` places an OFFER. Until the auction resolves the money is
    still shown as available, so approving two proposals in one sitting can
    commit more than exists — and a negative budget at kickoff is zero points
    for the whole matchday.
    """

    def test_a_second_approval_cannot_spend_money_the_first_already_committed(
        self, tmp_path, settings, api
    ):
        learner = BidLearner(db_path=tmp_path / "bids.db")
        for pid, player_id, bid in (("p1", "1", 30_000_000), ("p2", "2", 35_000_000)):
            learner.record_proposal(
                proposal_id=pid,
                player_id=player_id,
                player_name=f"Player {player_id}",
                bid=bid,
                market_value=bid,
                message="BUY",
            )

        api.get_market.return_value = [
            MagicMock(id="1", market_value=30_000_000, last_name="One"),
            MagicMock(id="2", market_value=35_000_000, last_name="Two"),
        ]
        api.get_team_info.return_value = {"budget": 40_000_000}

        first = handle_callback(
            _cb(pid="p1"),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )
        assert "bought" in first.lower()

        # Kickbase now shows an open offer, but the SAME budget as before.
        api.get_my_bids.return_value = [MagicMock(id="1", user_offer_price=30_000_000)]

        second = handle_callback(
            _cb(pid="p2"),
            "s3cret",
            settings=settings,
            learner=learner,
            api=api,
            league=MagicMock(),
        )

        assert api.buy_player.call_count == 1
        assert "not executed" in second.lower()
        assert learner.get_proposal("p2")["status"] == "failed"


class TestApprovedBuysReachTheLearningLoop:
    def test_an_approved_buy_records_a_pending_bid(self, learner, settings, api):
        """Otherwise resolve_auctions never writes an auction_outcomes row for
        it, and the learned-overbid calibration goes blind for approved buys.
        """
        with patch("rehoboam.learning.tracker.LearningTracker") as tracker_cls:
            handle_callback(
                _cb(),
                "s3cret",
                settings=settings,
                learner=learner,
                api=api,
                league=MagicMock(),
            )
        tracker_cls.return_value.record_bid_placed.assert_called_once()
        args = tracker_cls.return_value.record_bid_placed.call_args[0]
        assert args[0].id == "6080"
        assert args[1] == 32_608_485
