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
    _result, state = _run(lambda pid, price, at: 9_999_999)

    assert "target" not in state.squad


def test_a_bid_above_the_real_price_wins():
    _result, state = _run(lambda pid, price, at: 11_000_000)

    assert "target" in state.squad


def test_the_winner_pays_its_own_bid_not_the_rivals_price():
    """Outbidding costs more than the rival paid. Charging the rival's price
    would give us the upside of competition with none of its cost."""
    _result, state = _run(lambda pid, price, at: 11_000_000)

    assert state.budget == 100_000_000 - 11_000_000


def test_without_a_bid_fn_every_wanted_player_is_still_won():
    """The shipped path is unchanged — competition is opt-in."""
    _result, state = _run(None)

    assert "target" in state.squad
    assert state.budget == 100_000_000 - 10_000_000
