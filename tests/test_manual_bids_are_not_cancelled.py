"""The bot must not cancel a bid it did not place (REH-115).

On 2026-09-01 the 08:00 session cancelled Marco's own offer on Conrad Harder:

    Keep: 0  Cancel: 1
    Reason: Bid 34.4% over market value - above its ceiling
    Canceling bid on Conrad Harder...

Harder has zero rows in `trade_proposals`, `pending_bids` and
`auction_outcomes` — the bot never bid on him.

`evaluate_active_bids` iterates every live offer, because `get_my_bids` is
`get_market` filtered by "do we hold an offer" and cannot tell who placed it.
REH-111 then judged each bid by its recorded tier, and a bid with none fell to
`untiered_price_ceiling` — a flat 25% cap. Harder was 34.4% over, so it went.

The TODO on that function framed untiered as "rows written before the column
existed". That missed the other, permanent case: **a human placed it**. A
manual bid can never carry a tier, so it would never stop being cancelled.

The rule is simpler than the ceiling question it looked like: an offer with no
`pending_bids` row is not the bot's to withdraw. Marco's judgement is an input,
not a candidate for review.
"""

from __future__ import annotations

import pytest

from rehoboam.bid_evaluator import BidEvaluator
from rehoboam.config import Settings
from rehoboam.kickbase_client import MarketPlayer

LEAGUE = "1933872"


def _settings():
    return Settings(kickbase_email="test@example.com", kickbase_password="x")


def _offer(player_id, market_value, our_bid, *, status=0, avg=60.0):
    return MarketPlayer(
        id=player_id,
        first_name="Conrad",
        last_name="Harder",
        position="Forward",
        team_id="15",
        team_name="",
        price=market_value,
        market_value=market_value,
        points=0,
        average_points=avg,
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


# 34.4% over market value — past the flat 25% that cancelled it.
HARDER = _offer("9999", market_value=10_000_000, our_bid=13_440_000)


def _evaluate(offer, *, bot_placed_ids, bid_tiers=None, trends=None):
    evaluator = BidEvaluator(_Api([offer]), _settings())
    return evaluator.evaluate_active_bids(
        LEAGUE,
        player_trends=trends or {},
        for_profit=True,
        bid_tiers=bid_tiers or {},
        bot_placed_ids=bot_placed_ids,
    )[0]


class TestAManualBidIsLeftAlone:
    def test_the_real_harder_bid_is_kept(self):
        """The exact 2026-09-01 cancellation, now a no-op."""
        result = _evaluate(HARDER, bot_placed_ids=set())

        assert result.recommendation == "KEEP", result.reason

    def test_the_reason_says_why(self):
        result = _evaluate(HARDER, bot_placed_ids=set())

        assert "manual" in result.reason.lower() or "not placed by" in result.reason.lower()

    def test_a_falling_manual_bid_is_still_kept(self):
        """Flip economics are the bot's rules for the bot's bids."""
        trends = {"9999": {"trend": "falling", "trend_pct": -30.0, "peak_value": 20_000_000}}

        result = _evaluate(HARDER, bot_placed_ids=set(), trends=trends)

        assert result.recommendation == "KEEP", result.reason

    def test_a_manual_bid_on_an_injured_player_is_still_kept(self):
        """Even the injury rule: Marco can see the injury and bid anyway."""
        injured = _offer("9999", market_value=10_000_000, our_bid=13_440_000, status=1)

        result = _evaluate(injured, bot_placed_ids=set())

        assert result.recommendation == "KEEP", result.reason


class TestTheBotStillPolicesItsOwnBids:
    def test_an_untiered_bot_bid_is_still_evaluated(self):
        """The guard keys on provenance, not on the tier being present."""
        result = _evaluate(HARDER, bot_placed_ids={"9999"})

        assert result.recommendation == "CANCEL"

    def test_a_tiered_bot_bid_at_its_ceiling_is_kept(self):
        """REH-111 still holds for bids the bot placed."""
        result = _evaluate(HARDER, bot_placed_ids={"9999"}, bid_tiers={"9999": "must_have"})

        assert result.recommendation == "KEEP", result.reason


class TestTheDefaultIsSafe:
    def test_omitting_the_set_does_not_silently_cancel_manual_bids(self):
        """A caller that forgets the argument must not resume cancelling.

        The production wiring passes it, but this is the money path — the
        default has to fail toward leaving a human's bid alone.
        """
        evaluator = BidEvaluator(_Api([HARDER]), _settings())

        result = evaluator.evaluate_active_bids(LEAGUE, player_trends={}, for_profit=True)[0]

        assert result.recommendation == "KEEP", result.reason


@pytest.mark.parametrize("pct", [26.0, 34.4, 80.0])
def test_no_manual_bid_is_cancelled_at_any_overbid(pct):
    offer = _offer("9999", market_value=10_000_000, our_bid=int(10_000_000 * (1 + pct / 100)))

    assert _evaluate(offer, bot_placed_ids=set()).recommendation == "KEEP"


class TestTheSessionPassesProvenance:
    """A guard the session never populates is a guard that does nothing.

    `auction_outcomes.winning_bid` sat NULL for a season because a writer
    existed and nothing called it (REH-86). Same trap, so the wiring is proved.
    """

    def test_the_session_supplies_bot_placed_ids(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import patch

        from rehoboam.auto_trader import AutoTrader
        from rehoboam.bid_learner import BidLearner

        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)

        trader = AutoTrader(api=_Api([HARDER]), settings=_settings(), dry_run=True)
        trader.learner = BidLearner(db_path=tmp_path / "bid_learning.db")
        trader.learner.add_pending_bid(
            player_id="849",
            player_name="Kleindienst",
            our_bid=1,
            asking_price=1,
            our_overbid_pct=0.0,
            timestamp=1.0,
        )

        captured = {}

        def _spy(self, league, player_trends=None, for_profit=True, **kwargs):
            captured.update(kwargs)
            return []

        with patch.object(BidEvaluator, "evaluate_active_bids", _spy):
            trader._evaluate_open_bids(SimpleNamespace(id="L"), player_trends={})

        assert captured.get("bot_placed_ids") == {"849"}
