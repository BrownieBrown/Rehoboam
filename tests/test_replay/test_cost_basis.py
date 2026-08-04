"""REH-68: the replay has to remember what it paid.

`state.buy()` debited the budget and discarded the price, so profit was
uncomputable and no flip logic could exist. Profit is the difference between
what we paid and what a player is worth now, so a cost basis is the
prerequisite for modelling the sell side at all.
"""

from __future__ import annotations

from rehoboam.replay.state import ReplayPlayer, ReplayState


def test_buying_records_what_we_paid():
    state = ReplayState(budget=50_000_000)

    state.buy(ReplayPlayer(id="p", position="Forward"), 7_000_000, at=1000.0)

    assert state.squad["p"].buy_price == 7_000_000


def test_buying_records_when_we_bought():
    """Hold duration gates a flip — the live ProfitTrader caps holds at 7 days."""
    state = ReplayState(budget=50_000_000)

    state.buy(ReplayPlayer(id="p", position="Forward"), 7_000_000, at=1234.0)

    assert state.squad["p"].bought_at == 1234.0


def test_a_player_with_no_basis_is_not_treated_as_pure_profit():
    """The opening squad was ASSIGNED, not bought. A basis of zero would make
    every opening-squad sale read as a 100% gain and hand the replay a fortune
    it never earned."""
    player = ReplayPlayer(id="p", position="Forward")

    assert player.buy_price is None, "unknown basis must be explicit, not 0"
