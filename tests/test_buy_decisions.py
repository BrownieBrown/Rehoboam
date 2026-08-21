"""REH-86: record what the bot decided, not only what it did.

Four analyses in one day stalled on the same gap. REH-75 could not attribute
round trips because `flip_outcomes` records no motive. REH-72 could not
attribute the capital collapse because `manager_transfers` records no actor.
The bidding question ("how hard must we bid to win a EUR 20m player?") could not
be answered because `auction_outcomes.winning_bid` is empty on all 26 rows.

The schema was mostly right and mostly unfilled. These tests pin the two
additions that make next season answerable:

* `record_buy_decision` — every candidate the bot evaluated and declined, with
  the reason. Without it, "the bot bought nothing for weeks" cannot be
  distinguished from "the bot saw nothing worth buying".
* `resolve_auction_winners` — fills `winning_bid` / `winner_user_id` on lost
  auctions from the transfer feed the session already ingests. Turns "we lost
  at 1.0% overbid" into "we lost at 1.0% and it took 24%".
"""

import sqlite3

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


def _rows(learner, table):
    with sqlite3.connect(learner.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


# --- declined candidates -------------------------------------------------


def test_a_declined_candidate_is_recorded_with_its_reason(learner):
    learner.record_buy_decision(
        player_id="p1",
        player_name="Declined Player",
        decision="declined",
        reason="below_ep_threshold",
        marginal_ep_gain=12.5,
        asking_price=5_000_000,
        market_value=4_800_000,
        budget_ceiling=90_000_000,
        timestamp=1_000.0,
    )
    rows = _rows(learner, "buy_decisions")
    assert len(rows) == 1
    assert rows[0]["decision"] == "declined"
    assert rows[0]["reason"] == "below_ep_threshold"
    assert rows[0]["marginal_ep_gain"] == pytest.approx(12.5)


def test_the_affordability_case_is_distinguishable_from_disinterest(learner):
    """The distinction the Lukeba bid turns on: we wanted him and could not
    afford him, which is a different failure from not wanting him."""
    for pid, reason, gain, ceiling in (
        ("cheap", "below_ep_threshold", 5.0, 90_000_000),
        ("wanted", "unaffordable", 190.0, 4_687_915),
    ):
        learner.record_buy_decision(
            player_id=pid,
            player_name=pid,
            decision="declined",
            reason=reason,
            marginal_ep_gain=gain,
            asking_price=21_000_000,
            market_value=21_000_000,
            budget_ceiling=ceiling,
            timestamp=1_000.0,
        )
    by_reason = {r["reason"]: r for r in _rows(learner, "buy_decisions")}
    assert by_reason["unaffordable"]["marginal_ep_gain"] == pytest.approx(190.0)
    assert by_reason["unaffordable"]["budget_ceiling"] == 4_687_915


def test_recording_a_decision_never_raises_on_a_missing_optional(learner):
    """Learning writes must never break the trading path."""
    learner.record_buy_decision(
        player_id="p2",
        player_name="Sparse",
        decision="declined",
        reason="contested_skip",
        marginal_ep_gain=None,
        asking_price=None,
        market_value=None,
        budget_ceiling=None,
        timestamp=1_000.0,
    )
    assert len(_rows(learner, "buy_decisions")) == 1


# --- who actually won the auction ---------------------------------------


AUCTION_TS = 1777888800.0  # 2026-05-04T10:00Z, one day before the fixture transfer


def _lost_auction(learner, player_id="p9", our_bid=21_338_887, ts=AUCTION_TS):
    from rehoboam.bid_learner import AuctionOutcome

    learner.record_outcome(
        AuctionOutcome(
            player_id=player_id,
            player_name="Lukeba",
            our_bid=our_bid,
            asking_price=21_127_611,
            our_overbid_pct=1.0,
            won=False,
            timestamp=ts,
        )
    )


def _transfer(learner, player_id, manager_id, price, dt):
    learner.record_manager_transfers(
        [
            {
                "league_id": "L1",
                "manager_id": manager_id,
                "transfer_dt": dt,
                "player_id": player_id,
                "player_name": "Lukeba",
                "transfer_type": 1,
                "transfer_price": price,
            }
        ]
    )


def test_a_lost_auction_learns_what_the_winner_paid(learner):
    _lost_auction(learner)
    _transfer(learner, "p9", "rival-1", 26_200_000, "2026-05-05T10:00:00Z")

    filled = learner.resolve_auction_winners(window_days=3.0)

    assert filled == 1
    row = _rows(learner, "auction_outcomes")[0]
    assert row["winning_bid"] == 26_200_000
    assert row["winner_user_id"] == "rival-1"


def test_a_transfer_outside_the_window_is_not_attributed(learner):
    """A player traded weeks later says nothing about our auction. Refusing to
    match is the safe direction: a wrong winning_bid would corrupt the exact
    distribution this exists to measure."""
    _lost_auction(learner)
    _transfer(learner, "p9", "rival-1", 26_200_000, "2026-09-01T10:00:00Z")

    assert learner.resolve_auction_winners(window_days=3.0) == 0
    assert _rows(learner, "auction_outcomes")[0]["winning_bid"] is None


def test_already_resolved_rows_are_not_rewritten(learner):
    _lost_auction(learner)
    _transfer(learner, "p9", "rival-1", 26_200_000, "2026-05-05T10:00:00Z")
    assert learner.resolve_auction_winners(window_days=3.0) == 1
    assert learner.resolve_auction_winners(window_days=3.0) == 0


def test_won_auctions_are_left_alone(learner):
    """We already know what the winning bid was: ours."""
    from rehoboam.bid_learner import AuctionOutcome

    learner.record_outcome(
        AuctionOutcome(
            player_id="p8",
            player_name="Won",
            our_bid=1_000_000,
            asking_price=900_000,
            our_overbid_pct=11.1,
            won=True,
            timestamp=AUCTION_TS,
        )
    )
    _transfer(learner, "p8", "someone", 999, "2026-05-05T10:00:00Z")
    assert learner.resolve_auction_winners(window_days=3.0) == 0
