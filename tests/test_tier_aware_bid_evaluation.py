"""An open bid must be judged by the ceiling it was priced against (REH-111).

`bid_ceiling.py` (REH-99) lets a `must_have` bid reach market value +30%.
`bid_evaluator.py` then re-read every open bid with `for_profit=True` and
cancelled anything over +25% as "too expensive for flip" — a flip's economics
applied to a bid that was never a flip. The bot withdrew its own best offers:

    Kleindienst  2026-08-30 08:12  ours 24,748,891 on MV 19,038,891  (+30.0%)
    Badé         2026-08-30 08:12  ours 17,126,503 on MV 13,176,503  (+30.0%)

Both were cancelled by the 20:00 session that evening ("Keep: 1, Cancel: 2").
Seven hours later Kupka took Kleindienst for 21,548,071 — 3,200,820 BELOW the
bid we had just withdrawn. This is the mechanism behind the REH-102 alarm:
losing while holding the best price, because the offer was gone.

The property asserted here mirrors `test_bid_ceiling_agreement.py`: **anything
the bidder was allowed to offer, the evaluator must be willing to leave
standing.** Only a bid above its own tier's ceiling may be cancelled on price.
"""

import pytest

from rehoboam.bid_evaluator import BidEvaluator
from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.services.bid_ceiling import Tier

LEAGUE = "1933872"


def _settings():
    return Settings(kickbase_email="test@example.com", kickbase_password="x")


def _listing(player_id: str, name: str, market_value: int, our_bid: int) -> MarketPlayer:
    """A Kickbase-listed player carrying our open offer."""
    return MarketPlayer(
        id=player_id,
        first_name="",
        last_name=name,
        position="Forward",
        team_id="15",
        team_name="",
        price=market_value,
        market_value=market_value,
        points=0,
        average_points=60.0,
        status=0,
        seller_user_id=None,
        offer_count=1,
        user_offer_price=our_bid,
        user_offer_id="3616202",
    )


class _FakeApi:
    """Only the two calls `BidEvaluator` makes."""

    def __init__(self, listings):
        self._listings = listings

    def get_my_bids(self, league):
        return list(self._listings)

    def get_market(self, league):
        return list(self._listings)


# The real Kleindienst listing the bot cancelled itself out of.
KLEINDIENST = _listing("849", "Kleindienst", market_value=19_038_891, our_bid=24_748_891)


def _evaluate(listing, tiers):
    evaluator = BidEvaluator(_FakeApi([listing]), _settings())
    return evaluator.evaluate_active_bids(
        LEAGUE, player_trends={}, for_profit=True, bid_tiers=tiers
    )[0]


class TestABidIsJudgedByItsOwnCeiling:
    def test_a_must_have_bid_at_its_ceiling_is_kept(self):
        """+30% is the must_have ceiling, so it is not 'too expensive'."""
        result = _evaluate(KLEINDIENST, {"849": Tier.MUST_HAVE.value})

        assert result.recommendation == "KEEP", result.reason

    def test_a_marginal_bid_above_its_ceiling_is_still_cancelled(self):
        """The guard must still fire — this is not a licence to overpay."""
        result = _evaluate(KLEINDIENST, {"849": Tier.MARGINAL.value})

        assert result.recommendation == "CANCEL"
        assert "over market value" in result.reason

    @pytest.mark.parametrize("tier", list(Tier))
    def test_no_tier_is_cancelled_at_its_own_ceiling(self, tier):
        """The agreement property, across every tier the bidder can assign."""
        policy = _settings().bid_ceiling_policy()
        mv = 19_038_891
        listing = _listing("849", "Kleindienst", market_value=mv, our_bid=policy.max_bid(mv, tier))

        result = _evaluate(listing, {"849": tier.value})

        assert result.recommendation == "KEEP", result.reason


class TestTheTierSurvivesTheRoundTrip:
    """The evaluator can only honour a tier the DB actually kept."""

    def test_a_recorded_tier_comes_back_out(self, tmp_path):
        learner = BidLearner(db_path=tmp_path / "bid_learning.db")
        learner.add_pending_bid(
            player_id="849",
            player_name="Tim Kleindienst",
            our_bid=24_748_891,
            asking_price=19_038_891,
            our_overbid_pct=29.99,
            timestamp=1_788_077_520.0,
            market_value=19_038_891,
            tier=Tier.MUST_HAVE.value,
        )

        assert learner.get_pending_bids()[0]["tier"] == "must_have"

    def test_a_bid_recorded_without_a_tier_reads_back_as_none(self, tmp_path):
        """Pre-REH-111 rows, and paths that do not carry a tier."""
        learner = BidLearner(db_path=tmp_path / "bid_learning.db")
        learner.add_pending_bid(
            player_id="849",
            player_name="Tim Kleindienst",
            our_bid=24_748_891,
            asking_price=19_038_891,
            our_overbid_pct=29.99,
            timestamp=1_788_077_520.0,
            market_value=19_038_891,
        )

        assert learner.get_pending_bids()[0]["tier"] is None


class TestTheApprovedBidRecordsItsTier:
    """The approval path knows the tier — it must not drop it on the floor."""

    def test_record_bid_placed_persists_the_tier(self, tmp_path):
        from rehoboam.learning.tracker import LearningTracker

        learner = BidLearner(db_path=tmp_path / "bid_learning.db")
        listing = _listing("849", "Kleindienst", market_value=19_038_891, our_bid=24_748_891)

        LearningTracker(learner).record_bid_placed(listing, 24_748_891, tier=Tier.MUST_HAVE.value)

        assert learner.get_pending_bids()[0]["tier"] == "must_have"
