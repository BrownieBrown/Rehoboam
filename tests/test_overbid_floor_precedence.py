"""REH-89: the EP overbid floor must not override a deliberate risk reduction.

`SmartBidding.calculate_ep_bid` builds an overbid from a stack of signals, one
of which cuts the bid hard when the player's market value is falling
(`bidding_strategy.py`, trend-based reduction). Immediately afterwards the
"learned override" replaced that stack outright:

    overbid_pct = min(learned_pct, max_overbid)   # not min(learned_pct, stack)

The overriding value came from `BidLearner.get_ep_recommended_overbid`, whose
floor is `marginal_ep_gain * EP_FLOOR_PCT_PER_POINT`. That coefficient was
calibrated on the 0-100 index, where `max(floor, 8.0)` meant it only bound for
gains above ~27 and was therefore a rarely-triggered backstop. On the real
points scale of v2 every gain binds, so the backstop quietly became the
primary driver of bid size.

Observed live 2026-08-21/22 on Maximilian Rohr:

    stack=11.5% learned=21.1% applied=21.1%
      | EP-gain=70.5pts -> floor 21.1% | insufficient auction history

The trend logic saw -9.16 and cut to 11.5%; the floor pushed it back to 21.1%
and the bot bid EUR 11,587,747 against a EUR 9,567,747 market value that had
fallen 2.3% overnight.

Crucially the reason was "insufficient auction history": the learner had 26
auction outcomes but all from 2026-05-01..05-15, outside its 90-day window.
Off-season leaves that window empty, so season start -- when the bot spends
most -- is exactly when an unmeasured floor takes over.
"""

import sqlite3
import time

import pytest

from rehoboam.bid_learner import BidLearner
from rehoboam.bidding_strategy import SmartBidding


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bids.db")


def _seed_auctions(learner, n, won=True, age_days=1):
    ts = time.time() - age_days * 24 * 3600
    with sqlite3.connect(learner.db_path) as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO auction_outcomes "
                "(player_id, player_name, our_bid, asking_price, our_overbid_pct, "
                " won, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"p{i}", f"P{i}", 1_100_000, 1_000_000, 10.0, 1 if won else 0, ts),
            )


class TestFloorDefersWithoutEvidence:
    def test_no_recent_auctions_returns_zero_not_a_floor(self, learner):
        """With nothing measured, recommend nothing and let the stack stand."""
        out = learner.get_ep_recommended_overbid(
            asking_price=9_567_747,
            marginal_ep_gain=70.5,
            market_value=9_567_747,
            budget_ceiling=85_000_000,
        )
        assert out["recommended_overbid_pct"] == 0.0, out
        assert "defer" in out["reason"].lower(), out["reason"]

    def test_stale_auctions_outside_the_window_do_not_count(self, learner):
        """The live failure: 26 outcomes, all ~100 days old, window empty."""
        _seed_auctions(learner, 26, age_days=100)
        out = learner.get_ep_recommended_overbid(
            asking_price=9_567_747,
            marginal_ep_gain=70.5,
            market_value=9_567_747,
            budget_ceiling=85_000_000,
        )
        assert out["recommended_overbid_pct"] == 0.0, out

    def test_recent_auctions_do_produce_a_recommendation(self, learner):
        """Once there IS evidence, the learner is allowed to speak again."""
        _seed_auctions(learner, 10, won=False, age_days=5)
        out = learner.get_ep_recommended_overbid(
            asking_price=9_567_747,
            marginal_ep_gain=70.5,
            market_value=9_567_747,
            budget_ceiling=85_000_000,
        )
        assert out["recommended_overbid_pct"] > 0.0, out
        assert "insufficient" not in out["reason"].lower()


class TestFallingValueGuardSurvives:
    def test_falling_market_value_reduction_is_not_overridden(self, learner):
        """The Rohr case, end to end through the real bidding path."""
        bidding = SmartBidding(bid_learner=learner)

        falling = bidding.calculate_ep_bid(
            asking_price=9_567_747,
            market_value=9_567_747,
            expected_points=89.1,
            marginal_ep_gain=70.5,
            confidence=0.7,
            current_budget=85_162_497,
            player_id="3284",
            trend_change_pct=-11.27,
        )
        flat = bidding.calculate_ep_bid(
            asking_price=9_567_747,
            market_value=9_567_747,
            expected_points=89.1,
            marginal_ep_gain=70.5,
            confidence=0.7,
            current_budget=85_162_497,
            player_id="3284",
            trend_change_pct=0.0,
        )

        assert falling.recommended_bid < flat.recommended_bid, (
            "a player whose market value is falling 11% must be bid on more "
            f"cautiously, got falling={falling.recommended_bid:,} "
            f"flat={flat.recommended_bid:,}"
        )

    def test_bid_stays_under_the_observed_live_overbid(self, learner):
        """21.1% on a falling value was the defect; the stack alone gives ~11.5%."""
        bidding = SmartBidding(bid_learner=learner)
        rec = bidding.calculate_ep_bid(
            asking_price=9_567_747,
            market_value=9_567_747,
            expected_points=89.1,
            marginal_ep_gain=70.5,
            confidence=0.7,
            current_budget=85_162_497,
            player_id="3284",
            trend_change_pct=-11.27,
        )
        overbid_pct = (rec.recommended_bid / 9_567_747 - 1) * 100
        assert overbid_pct < 20.0, f"overbid {overbid_pct:.1f}% still above the stack"
