"""REH-68: the replay must be able to LOSE an auction.

Until now every wanted player was won, at the price someone else really paid —
the single largest fidelity gap in the harness, and the reason every buy-side
number has been labelled an upper bound.

`player_transfers` has a price on all 6,610 type-2 rows (a counterparty on only
51%), so the defensible model is price-based: we win a listing only if our bid
exceeds what the real buyer actually paid, and we pay our own bid, not theirs.
Paying our bid is the pessimistic and correct choice — outbidding a rival costs
more than the rival paid, and pretending otherwise would hand us the upside of
competition without its cost.
"""

from __future__ import annotations

from rehoboam.replay.engine import Matchday, run_season
from rehoboam.replay.market import MarketListing
from rehoboam.replay.state import ReplayPlayer, ReplayState

DAY = 86400.0
POS = ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 4 + ["Forward"] * 2


class FakeMarket:
    def __init__(self, listings):
        self.listings = listings

    def available_before(self, at):
        return [x for x in self.listings if x.transfer_at < at]


def _squad():
    return {str(i): ReplayPlayer(id=str(i), position=POS[i], team_id=str(i)) for i in range(11)}


def _run(bid_fn):
    state = ReplayState(budget=100_000_000, squad=_squad())
    result = run_season(
        state=state,
        market=FakeMarket([MarketListing(player_id="target", price=10_000_000, transfer_at=DAY)]),
        matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
        score_fn=lambda pid, at: 200.0 if pid == "target" else 10.0,
        mv_fn=lambda pid, at: 10_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: pid,
        bid_fn=bid_fn,
    )
    return result, state


def test_a_bid_below_the_real_price_loses_the_auction():
    """The rival paid 10,000,000. Bidding under that must not win."""
    _result, state = _run(lambda pid, price, at, gain, budget, squad_size: 9_999_999)

    assert "target" not in state.squad


def test_a_bid_above_the_real_price_wins():
    _result, state = _run(lambda pid, price, at, gain, budget, squad_size: 11_000_000)

    assert "target" in state.squad


def test_the_winner_pays_its_own_bid_not_the_rivals_price():
    """Outbidding costs more than the rival paid. Charging the rival's price
    would give us the upside of competition with none of its cost."""
    _result, state = _run(lambda pid, price, at, gain, budget, squad_size: 11_000_000)

    assert state.budget == 100_000_000 - 11_000_000


def test_without_a_bid_fn_every_wanted_player_is_still_won():
    """The shipped path is unchanged — competition is opt-in."""
    _result, state = _run(None)

    assert "target" in state.squad
    assert state.budget == 100_000_000 - 10_000_000


def test_bid_fn_receives_the_marginal_gain_and_the_current_budget():
    """A bid is a function of how much the player improves the eleven and what
    we can afford — SmartBidding.calculate_ep_bid needs both. Without them the
    hook can only express a flat willingness to pay, which is not the bot."""
    seen: dict = {}

    def bid(pid, price, at, gain, budget, squad_size):
        seen.update(pid=pid, price=price, gain=gain, budget=budget, squad_size=squad_size)
        return 0  # lose, so the run is unaffected

    _run(bid)

    assert seen["pid"] == "target"
    assert seen["price"] == 10_000_000
    assert seen["gain"] > 0.0, "target scores 200 against a squad of 10s"
    assert seen["budget"] == 100_000_000
    assert seen["squad_size"] == 11


def test_the_ep_bid_fn_bids_more_for_a_must_have_than_for_a_marginal_gain():
    """Wires SmartBidding in as the bidder, which is what finally makes the
    tier thresholds REH-55 re-tuned (70/53/43) matter to the replay at all —
    until now the harness bought at the listed price and never called it.
    """
    from rehoboam.replay.driver import make_ep_bid_fn

    bid_fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 10_000_000,
        score_fn=lambda pid, at: 100.0,
        median_move_fn=lambda at: 10_000_000,
        in_season_min_moves=2,
    )

    # squad_size=14 -> 1 slot left -> capital_reserve's last-slot case (0
    # reserve): this test is about tiers, not pacing, so the reserve must not
    # confound it.
    must_have = bid_fn("p", 10_000_000, 0.0, 80.0, 50_000_000, 14)
    marginal = bid_fn("p", 10_000_000, 0.0, 5.0, 50_000_000, 14)

    assert must_have > marginal


def test_the_ep_bid_fn_never_bids_beyond_the_budget():
    from rehoboam.replay.driver import make_ep_bid_fn

    bid_fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 10_000_000,
        score_fn=lambda pid, at: 100.0,
        median_move_fn=lambda at: 10_000_000,
        in_season_min_moves=2,
    )

    # squad_size=14 -> 0 reserve (see above) -> this exercises the budget cap
    # alone, not the pacing cap.
    assert bid_fn("p", 10_000_000, 0.0, 80.0, 3_000_000, 14) <= 3_000_000
