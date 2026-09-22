"""A placed bid is a commitment (autonomous-wallet spec §2).

Between 2026-08-30 and 2026-09-01 `bid_evaluator` withdrew five offers Marco
had approved, each re-judged against a ceiling the safety gate had already
enforced at placement:

    Castello Jr.  EUR 20,089,389  "39.4% over market value - above its ceiling"
    Harder        EUR  6,424,255  "34.4% over market value - above its ceiling"
    Kleindienst   EUR 24,748,891  "30.0% over market value - too expensive for flip"
    Badé          EUR 17,126,503  same reason, twice

Kleindienst and Castello were auctions the bot would have won. Price never
cancels. The one thing that does is the player becoming unfieldable.
"""

from __future__ import annotations

import inspect

import pytest

from rehoboam.bid_evaluator import BidEvaluator
from rehoboam.config import UNAVAILABLE_STATUSES, Settings
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.services.bid_ceiling import Tier

LEAGUE = "1933872"


def _settings():
    return Settings(kickbase_email="test@example.com", kickbase_password="x")


def _offer(player_id, market_value, our_bid, *, status=0):
    return MarketPlayer(
        id=player_id,
        first_name="Lukeba",
        last_name="Castello Jr.",
        position="Defender",
        team_id="15",
        team_name="",
        price=market_value,
        market_value=market_value,
        points=0,
        average_points=60.0,
        status=status,
        seller_user_id=None,
        offer_count=1,
        user_offer_price=our_bid,
        user_offer_id="3616202",
    )


class _Api:
    def __init__(self, offers):
        self._offers = offers

    def get_my_bids(self, league):
        return list(self._offers)

    def get_market(self, league):
        return list(self._offers)


# The real 2026-09-01 20:00 cancellation: EUR 20,089,389 against a market
# value of EUR 14,411,000 — 39.4% over, past every tier's ceiling.
CASTELLO = _offer("7224", market_value=14_411_000, our_bid=20_089_389)


def _evaluate(offer, *, bot_placed_ids, bid_tiers=None, trends=None):
    evaluator = BidEvaluator(_Api([offer]), _settings())
    return evaluator.evaluate_active_bids(
        LEAGUE,
        player_trends=trends or {},
        bid_tiers=bid_tiers or {},
        bot_placed_ids=bot_placed_ids,
    )[0]


class TestPriceNeverCancels:
    def test_the_real_castello_bid_is_held(self):
        result = _evaluate(
            CASTELLO, bot_placed_ids={"7224"}, bid_tiers={"7224": Tier.MUST_HAVE.value}
        )

        assert result.recommendation == "KEEP", result.reason
        assert "held" in result.reason

    @pytest.mark.parametrize("tier", list(Tier))
    def test_every_tier_is_held_far_above_its_ceiling(self, tier):
        result = _evaluate(CASTELLO, bot_placed_ids={"7224"}, bid_tiers={"7224": tier.value})

        assert result.recommendation == "KEEP", result.reason

    def test_an_untiered_bot_bid_is_held(self):
        """Rows predating the tier column: still the bot's, still a commitment."""
        result = _evaluate(CASTELLO, bot_placed_ids={"7224"})

        assert result.recommendation == "KEEP", result.reason

    def test_a_falling_trend_does_not_cancel(self):
        """Flip economics decide whether to PLACE a bid, never whether to pull one."""
        trends = {"7224": {"trend": "falling", "trend_pct": -30.0, "peak_value": 30_000_000}}

        result = _evaluate(CASTELLO, bot_placed_ids={"7224"}, trends=trends)

        assert result.recommendation == "KEEP", result.reason
        assert result.is_falling is True

    def test_the_reason_still_shows_the_premium(self):
        """The number is still worth seeing in the log — it just decides nothing."""
        result = _evaluate(
            CASTELLO, bot_placed_ids={"7224"}, bid_tiers={"7224": Tier.MUST_HAVE.value}
        )

        assert "+39.4%" in result.reason


class TestOnlyTheUnfieldableCancel:
    @pytest.mark.parametrize("status", sorted(UNAVAILABLE_STATUSES))
    def test_an_unavailable_player_cancels_a_bot_bid(self, status):
        offer = _offer("7224", market_value=14_411_000, our_bid=20_089_389, status=status)

        result = _evaluate(offer, bot_placed_ids={"7224"})

        assert result.recommendation == "CANCEL"
        assert result.is_injured is True
        assert str(status) in result.reason

    @pytest.mark.parametrize("status", [2])
    def test_a_status_the_lineup_would_still_field_does_not_cancel(self, status):
        """Status 2 is "uncertain"; the availability model, h2h and the

        evaluator all still field it — `h2h.project_squad` agrees with the
        evaluator's KEEP.
        """
        offer = _offer("7224", market_value=14_411_000, our_bid=20_089_389, status=status)

        assert _evaluate(offer, bot_placed_ids={"7224"}).recommendation == "KEEP"

    def test_an_unavailable_player_never_cancels_a_manual_bid(self):
        """Marco can see the injury and bid anyway (REH-115)."""
        offer = _offer("7224", market_value=14_411_000, our_bid=20_089_389, status=4)

        assert _evaluate(offer, bot_placed_ids=set()).recommendation == "KEEP"


class TestTheCeilingHelpersAreGone:
    def test_no_price_ceiling_survives_in_the_evaluator(self):
        import rehoboam.bid_evaluator as module

        assert not hasattr(module, "untiered_price_ceiling")
        assert not hasattr(BidEvaluator, "_price_ceiling")

    def test_for_profit_is_no_longer_an_argument(self):
        params = inspect.signature(BidEvaluator.evaluate_active_bids).parameters

        assert "for_profit" not in params


class TestTheLineupAndTheEvaluatorShareOneDefinition:
    def test_h2h_excludes_exactly_the_unavailable_statuses(self):
        from rehoboam.h2h import project_squad

        rows = [
            {"pn": "fit", "pos": 1, "ap": 50.0, "st": 0},
            {"pn": "knock", "pos": 2, "ap": 50.0, "st": 2},
            {"pn": "injured", "pos": 3, "ap": 99.0, "st": 4},
            {"pn": "long", "pos": 4, "ap": 99.0, "st": 256},
            {"pn": "weeks", "pos": 2, "ap": 99.0, "st": 1},
        ]

        fielded = {name for name, _position, _points in project_squad(rows, "me").eleven}

        assert fielded == {"fit", "knock"}

    def test_the_v2_availability_model_reads_the_same_set(self):
        from rehoboam.scoring.v2.availability import OUT_STATUSES

        assert OUT_STATUSES == UNAVAILABLE_STATUSES == frozenset({1, 4, 256})
