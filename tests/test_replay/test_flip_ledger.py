"""REH-71: flip P&L is cash, and cash is counted separately from points.

The attribution table decomposes a POINTS delta. Flip income is EUROS and
reaches the scoreboard only indirectly, through buys it funds. Mixing the two
would be a category error, so the ledger stands apart.
"""

from __future__ import annotations

from rehoboam.replay.engine import Matchday, run_season
from rehoboam.replay.state import ReplayPlayer, ReplayState

DAY = 86400.0
POS = ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 4 + ["Forward"] * 3


class NoMarket:
    def available_before(self, at):
        return []


def _squad_of_12(acquired: str = "bought"):
    return {
        str(i): ReplayPlayer(
            id=str(i),
            position=POS[i],
            team_id=str(i),
            buy_price=10_000_000,
            bought_at=0.0,
            acquired=acquired,
        )
        for i in range(12)
    }


def _run(*, current_mv: int, squad):
    state = ReplayState(budget=10_000_000, squad=squad)
    return run_season(
        state=state,
        market=NoMarket(),
        matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
        score_fn=lambda pid, at: 0.0 if pid == "11" else 100.0,
        mv_fn=lambda pid, at: current_mv if pid == "11" else 10_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: pid,
        profit_take_pct=15.0,
    )


def test_a_profitable_round_trip_is_recorded_with_its_pnl():
    result = _run(current_mv=12_000_000, squad=_squad_of_12())

    assert len(result.flips) == 1
    assert result.flips[0].proceeds - result.flips[0].buy_price == 2_000_000


def test_an_assigned_player_sold_is_not_a_round_trip():
    """The opening squad was assigned, not bought (state.py:96-100). Counting
    its disposals would inflate the count and make it incomparable to the real
    151 flips."""
    result = _run(current_mv=12_000_000, squad=_squad_of_12(acquired="assigned"))

    assert result.flips == []


def test_players_bought_during_the_season_are_marked_as_bought():
    state = ReplayState(budget=10_000_000, squad={})
    state.buy(ReplayPlayer(id="x", position="Forward"), 5_000_000, at=1.0)

    assert state.squad["x"].acquired == "bought"


def test_a_flip_bought_then_sold_closes_as_exactly_one_round_trip():
    """The end-to-end shape the ledger exists to count: the flip pass opens a
    position on matchday 1, the market moves, and the sell pass banks it on
    matchday 2. Anything other than a single record means the two halves
    disagree about what a round trip is.
    """
    from dataclasses import dataclass

    from rehoboam.replay.market import MarketListing

    @dataclass(frozen=True)
    class FakeCandidate:
        player_id: str
        market_value: int
        expected_appreciation: float
        max_bid: int

    class OneListing:
        def available_before(self, at):
            return [MarketListing(player_id="new", price=8_000_000, transfer_at=at - DAY)]

    # "new" is worth 8M when bought and 12M by matchday 2 -- a 50% gain, well
    # clear of the 15% take-profit threshold. He scores nothing, so he is never
    # a best-eleven starter and starter protection does not hold him.
    def mv_fn(pid, at):
        if pid != "new":
            return 10_000_000
        return 8_000_000 if at < 15 * DAY else 12_000_000

    state = ReplayState(budget=50_000_000, squad=_squad_of_12(acquired="assigned"))
    result = run_season(
        state=state,
        market=OneListing(),
        matchdays=[
            Matchday(day_number=1, kickoff=10 * DAY, points={}),
            Matchday(day_number=2, kickoff=20 * DAY, points={}),
        ],
        min_ep_gain=1_000_000.0,
        score_fn=lambda pid, at: 0.0 if pid == "new" else 100.0,
        mv_fn=mv_fn,
        # The squad already holds 3 forwards, the formation ceiling, so a
        # forward candidate would be refused as dead weight. Midfield has 4 of 5.
        position_fn=lambda pid: "Midfielder" if pid == "new" else "Forward",
        team_fn=lambda pid: pid,
        profit_take_pct=15.0,
        flip_buy_fn=lambda listings, at, budget, tv: [
            FakeCandidate(
                player_id="new",
                market_value=8_000_000,
                expected_appreciation=20.0,
                max_bid=9_000_000,
            )
        ],
        # Without this, the flip pass would re-buy "new" from the same stale
        # listing the instant _flip_sells closes him out, on the same
        # matchday -- the live bot's wash-trade guard (auto_trader.py:374,
        # Settings.wash_trade_block_hours, default 168h) exists precisely to
        # stop that.
        wash_trade_block_seconds=168.0 * 3600.0,
    )

    assert "new" not in state.squad
    assert len(result.flips) == 1
    assert result.flips[0].proceeds - result.flips[0].buy_price == 4_000_000
