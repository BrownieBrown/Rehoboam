"""Detecting the listings we have an open bid on.

`uoid` is a USER id — the manager holding the offer — confirmed against a live
listing on 2026-08-25 while our own bid was open. REH-95 claimed it was an
OFFER id and that the previous equality check therefore never matched; that was
wrong, and `get_my_bids()` was working the whole time.

Presence is used rather than equality because Kickbase only returns the field
on a listing we have bid on, so the two agree, and presence survives the value
changing shape. It also errs safe: reporting a bid we do not hold makes the bot
more conservative, missing one would let it overspend.
"""

from unittest.mock import MagicMock

from rehoboam.kickbase_client import MarketPlayer


def _listing(uoid=None, price=0):
    return MarketPlayer(
        id="1",
        first_name="A",
        last_name="B",
        position="Defender",
        team_id="40",
        team_name="X",
        price=1_000_000,
        market_value=1_000_000,
        points=0,
        average_points=0.0,
        status=0,
        user_offer_id=uoid,
        user_offer_price=price,
    )


class TestHasUserOffer:
    def test_a_listing_carrying_an_offer_id_is_ours(self):
        assert _listing(uoid="off-123").has_user_offer() is True

    def test_a_listing_with_no_offer_id_is_not(self):
        assert _listing().has_user_offer() is False

    def test_it_does_not_depend_on_the_value_matching_our_user_id(self):
        """Presence is the signal, whatever shape the value takes.

        Today `uoid` holds our user id. Testing presence rather than equality
        means this keeps working if that ever changes, without pretending to
        know which it is.
        """
        assert _listing(uoid="9f3c-some-other-shape").has_user_offer() is True


class TestGetMyBids:
    def _client_with(self, listings):
        from rehoboam.kickbase_client import KickbaseV4Client

        client = KickbaseV4Client.__new__(KickbaseV4Client)
        client.user = MagicMock(id="3616202")
        client.get_market = lambda league_id: listings
        return client

    def test_it_returns_the_listings_we_have_bid_on(self):
        from rehoboam.kickbase_client import KickbaseV4Client

        mine, theirs = _listing(uoid="off-1", price=5_000_000), _listing()
        client = self._client_with([mine, theirs])
        assert KickbaseV4Client.get_my_bids(client, "L") == [mine]

    def test_no_open_bids_returns_empty(self):
        from rehoboam.kickbase_client import KickbaseV4Client

        client = self._client_with([_listing(), _listing()])
        assert KickbaseV4Client.get_my_bids(client, "L") == []

    def test_the_open_bid_carries_the_price_the_budget_guard_needs(self):
        """pending_bid_total sums user_offer_price; an empty list summed to 0."""
        from rehoboam.kickbase_client import KickbaseV4Client

        mine = _listing(uoid="off-1", price=5_000_000)
        client = self._client_with([mine])
        bids = KickbaseV4Client.get_my_bids(client, "L")
        assert sum(b.user_offer_price for b in bids) == 5_000_000
