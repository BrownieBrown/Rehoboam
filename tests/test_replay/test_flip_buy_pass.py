"""REH-71: the replay buys for appreciation, not only for points.

The live bot buys flip candidates with whatever squad slots the EP pass leaves
(auto_trader.py:533). A flip never displaces a squad member -- the live bot does
not sell to make room for one -- so the pass simply stops at MAX_SQUAD_SIZE.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.replay.engine import Matchday, run_season
from rehoboam.replay.market import MarketListing
from rehoboam.replay.state import ReplayPlayer, ReplayState

DAY = 86400.0
# Fifteen slots, ordered so that _squad(12) is a legal eleven-fieldable squad
# (1 GK / 5 DEF / 4 MID / 2 FW) with midfield still below the formation ceiling
# of 5 -- otherwise `_would_create_dead_weight` refuses the candidate and every
# assertion below fails for a reason unrelated to what it is testing.
POS = (
    ["Goalkeeper"]
    + ["Defender"] * 5
    + ["Midfielder"] * 4
    + ["Forward"] * 3
    + ["Goalkeeper"]
    + ["Midfielder"]
)


@dataclass(frozen=True)
class FakeCandidate:
    player_id: str
    market_value: int
    expected_appreciation: float
    max_bid: int


class OneListing:
    def __init__(self, price: int) -> None:
        self.price = price

    def available_before(self, at):
        return [MarketListing(player_id="new", price=self.price, transfer_at=at - DAY)]


def _squad(n: int, basis: int = 10_000_000):
    return {
        str(i): ReplayPlayer(
            id=str(i), position=POS[i], team_id=str(i), buy_price=basis, bought_at=0.0
        )
        for i in range(n)
    }


def _run(*, squad_size: int, listing_price: int, max_bid: int, budget: int = 50_000_000):
    state = ReplayState(budget=budget, squad=_squad(squad_size))
    result = run_season(
        state=state,
        market=OneListing(listing_price),
        matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
        # High floor so the EP pass never buys; the flip pass is what we measure.
        min_ep_gain=1_000_000.0,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 10_000_000,
        # Midfield sits at 4 of a maximum 5, so the candidate is a real upgrade
        # slot rather than permanent dead weight.
        position_fn=lambda pid: "Midfielder",
        team_fn=lambda pid: pid,
        flip_buy_fn=lambda listings, at, budget, tv: [
            FakeCandidate(
                player_id="new",
                market_value=10_000_000,
                expected_appreciation=20.0,
                max_bid=max_bid,
            )
        ],
    )
    return result, state


def test_a_flip_candidate_within_the_ceiling_is_bought():
    _result, state = _run(squad_size=12, listing_price=9_000_000, max_bid=11_000_000)

    assert "new" in state.squad


def test_a_flip_is_not_bought_above_its_economic_ceiling():
    """Paying more than the flip can ever return is a guaranteed loss, so losing
    the listing to a rival is the correct outcome."""
    _result, state = _run(squad_size=12, listing_price=12_000_000, max_bid=11_000_000)

    assert "new" not in state.squad


def test_a_flip_never_displaces_a_squad_member():
    """At 15/15 the live bot does not sell to make room for a flip."""
    _result, state = _run(squad_size=15, listing_price=9_000_000, max_bid=11_000_000)

    assert "new" not in state.squad
    assert len(state.squad) == 15


def test_a_flip_is_skipped_when_it_would_leave_the_budget_negative():
    _result, state = _run(squad_size=12, listing_price=9_000_000, max_bid=11_000_000, budget=1_000)

    assert "new" not in state.squad


def test_flip_buying_is_off_by_default():
    """The shipped replay path must be unchanged until the run that enables it."""
    state = ReplayState(budget=50_000_000, squad=_squad(12))
    run_season(
        state=state,
        market=OneListing(9_000_000),
        matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
        min_ep_gain=1_000_000.0,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 10_000_000,
        position_fn=lambda pid: "Midfielder",
        team_fn=lambda pid: pid,
    )

    assert "new" not in state.squad
