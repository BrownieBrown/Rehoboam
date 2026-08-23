"""The safety gate — applied to everything that executes.

A public webhook will call this before spending money, so it is written and
tested before anything can reach it.
"""

from rehoboam.services.safety_gate import check_buy


def _ok_kwargs(**over):
    base = {
        "player_id": "6080",
        "bid": 32_608_485,
        "market_value": 32_285_629,
        "current_budget": 95_317_114,
        "free_slots": 2,
        "known_player_ids": {"6080", "859"},
        "max_overbid_pct": 8.0,
    }
    base.update(over)
    return base


class TestAcceptsAValidBuy:
    def test_a_normal_buy_passes(self):
        assert check_buy(**_ok_kwargs()).ok is True

    def test_a_passing_result_carries_no_reasons(self):
        assert check_buy(**_ok_kwargs()).reasons == []


class TestBudgetSafety:
    def test_a_buy_that_would_go_negative_is_refused(self):
        """Negative budget at kickoff scores ZERO for the whole matchday."""
        r = check_buy(**_ok_kwargs(current_budget=1_000_000))
        assert r.ok is False
        assert any("budget" in x.lower() for x in r.reasons)

    def test_spending_exactly_the_budget_is_allowed(self):
        r = check_buy(**_ok_kwargs(current_budget=32_608_485))
        assert r.ok is True


class TestOverbidCap:
    def test_a_bid_above_the_cap_is_refused(self):
        """32,285,629 * 1.08 = 34,868,479. A 38.3M bid is 18.7% over."""
        r = check_buy(**_ok_kwargs(bid=38_336_318))
        assert r.ok is False
        assert any("overbid" in x.lower() for x in r.reasons)

    def test_a_bid_at_exactly_the_cap_is_allowed(self):
        r = check_buy(**_ok_kwargs(bid=int(32_285_629 * 1.08)))
        assert r.ok is True


class TestSlots:
    def test_no_free_slot_is_refused(self):
        r = check_buy(**_ok_kwargs(free_slots=0))
        assert r.ok is False
        assert any("slot" in x.lower() for x in r.reasons)


class TestHallucinatedIdentifiers:
    def test_an_unknown_player_id_is_refused(self):
        """A model or a forged webhook can name a player we never sent."""
        r = check_buy(**_ok_kwargs(player_id="99999"))
        assert r.ok is False
        assert any("unknown player" in x.lower() for x in r.reasons)


class TestMultipleFailures:
    def test_all_failing_reasons_are_reported_not_just_the_first(self):
        r = check_buy(**_ok_kwargs(player_id="99999", free_slots=0, current_budget=1))
        assert r.ok is False
        assert len(r.reasons) >= 3


class TestInvalidMarketValue:
    """A delisted listing, malformed payload, or missing field can hand us a
    market value of 0. That must fail closed, not silently disable the
    overbid cap and let a bid of any size through."""

    def test_a_zero_market_value_is_refused(self):
        r = check_buy(**_ok_kwargs(market_value=0))
        assert r.ok is False
        assert any("market value" in x.lower() for x in r.reasons)

    def test_a_negative_market_value_is_refused(self):
        r = check_buy(**_ok_kwargs(market_value=-1))
        assert r.ok is False
        assert any("market value" in x.lower() for x in r.reasons)


class TestInvalidBid:
    """A forged webhook callback could send a non-positive bid. Every other
    check (budget, overbid cap) passes trivially for such a bid, so it needs
    its own explicit reason."""

    def test_a_negative_bid_is_refused(self):
        r = check_buy(**_ok_kwargs(bid=-1_000))
        assert r.ok is False
        assert any("bid" in x.lower() for x in r.reasons)

    def test_a_zero_bid_is_refused(self):
        r = check_buy(**_ok_kwargs(bid=0))
        assert r.ok is False
        assert any("bid" in x.lower() for x in r.reasons)
