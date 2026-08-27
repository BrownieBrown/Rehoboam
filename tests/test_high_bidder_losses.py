"""Tests for REH-102: losing an auction you were the high bidder in is an alarm.

Three August 2026 losses had our bid ABOVE what the winner paid:

    Rohr     2026-08-21   ours 12,305,344   winner 10,621,111   +1,684,233
    Rohr     2026-08-22   ours 11,587,747   winner 10,621,111     +966,636
    Gyamerah 2026-08-22   ours  8,616,278   winner  7,676,767     +939,511

All on Kickbase-listed players, where the highest bid should win. That is not
being outbid — the offer was gone. It went unnoticed for a week because
`auction_outcomes.won` is a boolean: it can record THAT we lost, never that we
lost while holding the best price.

`resolve_auction_winners` already learns the winning price from the transfer
feed a session or two later. That is the moment the anomaly becomes knowable,
so that is where it is now raised.
"""

import logging
import sqlite3

import pytest

from rehoboam.bid_learner import AuctionOutcome, BidLearner

LEAGUE = "1933872"
THEM = "1907519"
BUY = 1

# 2026-08-22T08:00:00Z, and a transfer close enough to fall inside the
# three-day attribution window resolve_auction_winners uses.
AUCTION_TS = 1_787_385_600.0
TRANSFER_DT = "2026-08-22T14:00:00Z"


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


def _lost_auction(learner, player_id, name, our_bid, *, won=False):
    learner.record_outcome(
        AuctionOutcome(
            player_id=player_id,
            player_name=name,
            our_bid=our_bid,
            asking_price=int(our_bid * 0.9),
            our_overbid_pct=11.1,
            won=won,
            timestamp=AUCTION_TS,
        )
    )


def _transfer(learner, player_id, name, price):
    learner.record_manager_transfers(
        [
            {
                "league_id": LEAGUE,
                "manager_id": THEM,
                "transfer_dt": TRANSFER_DT,
                "player_id": player_id,
                "player_name": name,
                "transfer_type": BUY,
                "transfer_price": price,
            }
        ]
    )


class TestHighBidderLosses:
    def test_a_loss_above_the_winning_price_is_reported(self, learner):
        """The Rohr case: ours 12,305,344, winner paid 10,621,111."""
        _lost_auction(learner, "rohr", "Maximilian Rohr", 12_305_344)
        _transfer(learner, "rohr", "Maximilian Rohr", 10_621_111)
        learner.resolve_auction_winners()

        anomalies = learner.high_bidder_losses()

        assert [a["player_name"] for a in anomalies] == ["Maximilian Rohr"]
        assert anomalies[0]["our_bid"] == 12_305_344
        assert anomalies[0]["winning_bid"] == 10_621_111
        assert anomalies[0]["margin"] == 1_684_233

    def test_a_genuine_outbid_is_not_an_anomaly(self, learner):
        """Kimmich: ours 60,447,397, winner paid 65,000,065. Simply outbid."""
        _lost_auction(learner, "kimmich", "Joshua Kimmich", 60_447_397)
        _transfer(learner, "kimmich", "Joshua Kimmich", 65_000_065)
        learner.resolve_auction_winners()

        assert learner.high_bidder_losses() == []

    def test_an_auction_we_won_is_not_an_anomaly(self, learner):
        _lost_auction(learner, "pav", "Aleksandar Pavlović", 38_336_318, won=True)
        _transfer(learner, "pav", "Aleksandar Pavlović", 30_000_000)
        learner.resolve_auction_winners()

        assert learner.high_bidder_losses() == []

    def test_an_unattributed_loss_is_not_an_anomaly(self, learner):
        """No transfer inside the window, so winning_bid stays NULL.

        Reporting these would be a guess, and the whole point of the alarm is
        that it fires only on evidence.
        """
        _lost_auction(learner, "schwolow", "Alexander Schwolow", 6_631_597)

        assert learner.high_bidder_losses() == []

    def test_an_equal_bid_is_not_an_anomaly(self, learner):
        """Ties go to whoever bid first; nothing to explain."""
        _lost_auction(learner, "tie", "Tied Player", 5_000_000)
        _transfer(learner, "tie", "Tied Player", 5_000_000)
        learner.resolve_auction_winners()

        assert learner.high_bidder_losses() == []

    def test_anomalies_are_ordered_by_margin(self, learner):
        """Worst first — the biggest unexplained loss is the one to chase."""
        _lost_auction(learner, "a", "Small Margin", 8_616_278)
        _transfer(learner, "a", "Small Margin", 7_676_767)  # +939,511
        _lost_auction(learner, "b", "Big Margin", 12_305_344)
        _transfer(learner, "b", "Big Margin", 10_621_111)  # +1,684,233
        learner.resolve_auction_winners()

        assert [a["player_name"] for a in learner.high_bidder_losses()] == [
            "Big Margin",
            "Small Margin",
        ]


class TestTheAlarmFires:
    def test_resolving_a_high_bidder_loss_logs_a_warning(self, learner, caplog):
        """Silence for a week is the actual defect being fixed here."""
        _lost_auction(learner, "rohr", "Maximilian Rohr", 12_305_344)
        _transfer(learner, "rohr", "Maximilian Rohr", 10_621_111)

        with caplog.at_level(logging.WARNING, logger="rehoboam.bid_learner"):
            learner.resolve_auction_winners()

        assert any(
            "Maximilian Rohr" in r.message and "high bidder" in r.message.lower()
            for r in caplog.records
        ), f"no alarm raised; got {[r.message for r in caplog.records]}"

    def test_a_genuine_outbid_is_silent(self, learner, caplog):
        _lost_auction(learner, "kimmich", "Joshua Kimmich", 60_447_397)
        _transfer(learner, "kimmich", "Joshua Kimmich", 65_000_065)

        with caplog.at_level(logging.WARNING, logger="rehoboam.bid_learner"):
            learner.resolve_auction_winners()

        assert not [r for r in caplog.records if "high bidder" in r.message.lower()]

    def test_resolution_still_returns_the_filled_count(self, learner):
        """The alarm must not change what the method is for."""
        _lost_auction(learner, "rohr", "Maximilian Rohr", 12_305_344)
        _transfer(learner, "rohr", "Maximilian Rohr", 10_621_111)

        assert learner.resolve_auction_winners() == 1

        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute(
                "SELECT winning_bid, winner_user_id FROM auction_outcomes"
            ).fetchone()
        assert row == (10_621_111, THEM)
