"""Kickbase caps one purchase at a share of your total worth (REH-118).

Discovered live on 2026-09-02 trying to bid EUR 70,564,078 on Olise:

    500 — {"err":5050,"errMsg":"ThirtyThreePercentRuleExceeded"}

Nothing in the codebase had ever heard of this rule, so the bot could propose
a player it is structurally incapable of buying and the only way to find out
was to tap Approve and get a failure.

The basis is total worth — team value plus budget — which fits every data
point available:

    2026-08-30  worth 166,119,591  cap 54,819,465   Orban   bid 40,386,341  accepted
    2026-08-31  worth 166,648,628  cap 54,994,047   Tapsoba bid 41,702,302  accepted
    2026-09-02  worth 154,902,319  cap 51,117,765   Olise   bid 70,564,078  REJECTED

The percentage is a `Settings` field because it is Kickbase's number, not
ours, and observing it from one rejection is not the same as knowing it.

Note what this rule does to the obvious plan. Selling to raise cash moves
money from team value to budget, so it does NOT raise the cap — and if the
real basis is team value alone it actively LOWERS it. Selling nine players to
afford Olise would have made him less affordable, not more.
"""

from __future__ import annotations

import pytest

from rehoboam.config import Settings
from rehoboam.services.safety_gate import check_buy

SETTINGS = Settings(kickbase_email="test@example.com", kickbase_password="x")
POLICY = SETTINGS.bid_ceiling_policy()

# The real 2026-09-02 position.
WORTH = 154_902_319
OLISE_MV = 64_976_131
OLISE_BID = 70_564_078


def _check(bid, *, worth, market_value=OLISE_MV, budget=200_000_000, **kw):
    return check_buy(
        player_id="8329",
        bid=bid,
        market_value=market_value,
        current_budget=budget,
        free_slots=4,
        known_player_ids=["8329"],
        tier="must_have",
        ceiling_policy=POLICY,
        total_worth=worth,
        max_single_buy_pct=SETTINGS.max_single_buy_pct_of_worth,
        **kw,
    )


class TestTheOliseRejection:
    def test_the_real_bid_is_refused(self):
        result = _check(OLISE_BID, worth=WORTH)

        assert not result.ok
        assert any("33" in r or "worth" in r.lower() for r in result.reasons), result.reasons

    def test_even_the_asking_price_is_refused(self):
        """It was never a bidding problem — he is above the cap unbid."""
        result = _check(OLISE_MV, worth=WORTH)

        assert not result.ok

    def test_the_reason_says_what_would_be_allowed(self):
        result = _check(OLISE_BID, worth=WORTH)
        text = " ".join(result.reasons)

        assert f"{int(WORTH * 0.33):,}" in text


class TestTheAcceptedHistory:
    """Bids Kickbase actually took must keep passing, or the cap is too tight."""

    @pytest.mark.parametrize(
        ("name", "worth", "bid"),
        [
            ("Orban", 166_119_591, 40_386_341),
            ("Tapsoba", 166_648_628, 41_702_302),
        ],
    )
    def test_a_historically_accepted_bid_still_passes(self, name, worth, bid):
        result = _check(bid, worth=worth, market_value=int(bid / 1.3))

        assert result.ok, f"{name}: {result.reasons}"


class TestTheBoundary:
    def test_a_bid_exactly_at_the_cap_is_allowed(self):
        cap = int(WORTH * 0.33)

        assert _check(cap, worth=WORTH, market_value=cap).ok

    def test_one_euro_over_the_cap_is_refused(self):
        cap = int(WORTH * 0.33)

        assert not _check(cap + 1, worth=WORTH, market_value=cap).ok


class TestItIsOptional:
    def test_omitting_total_worth_skips_the_check(self):
        """Callers that genuinely do not know their worth must not be blocked.

        Same convention as the club limit above it: an input the caller cannot
        supply is not treated as a violation.
        """
        result = check_buy(
            player_id="8329",
            bid=OLISE_BID,
            market_value=OLISE_MV,
            current_budget=200_000_000,
            free_slots=4,
            known_player_ids=["8329"],
            tier="must_have",
            ceiling_policy=POLICY,
        )

        assert result.ok, result.reasons


def test_selling_to_fund_a_buy_does_not_raise_the_cap():
    """The trap this rule sets, pinned as a fact.

    Selling moves money from team value to budget. Worth is unchanged, so the
    cap is unchanged — the nine-player sell-off planned on 2026-09-02 would
    have gutted the squad and left Olise exactly as unaffordable.
    """
    before = _check(OLISE_BID, worth=WORTH)
    # Sell EUR 79.6m of players: team value down, budget up, worth identical.
    after = _check(OLISE_BID, worth=WORTH)

    assert before.ok == after.ok is False


class TestAnUnknownTeamValueDisablesTheCheck:
    """Falling back to the budget alone would refuse every large buy.

    `get_team_info` derives team value by summing squad market values, so a
    failed squad fetch yields 0 — and a cap of a third of the WALLET is not
    the rule, it is a much tighter accidental one.
    """

    def test_zero_worth_does_not_block(self):
        assert _check(OLISE_BID, worth=0).ok

    def test_none_worth_does_not_block(self):
        assert _check(OLISE_BID, worth=None).ok
