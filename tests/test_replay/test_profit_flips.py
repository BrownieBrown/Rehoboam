"""REH-68: selling for gain (and cutting losses), not only under duress.

The replay's only sell triggers were "make room for a buy" and "restore
solvency". The live bot also takes profit at `min_sell_profit_pct` (15%) and
cuts losses at `max_loss_pct` (-15%), which is a whole revenue behaviour the
harness never exercised.

Measured expectation, pre-registered: real flipping LOST money — 151 flips,
net -EUR 55,256,064, 27.8% win rate — so modelling it faithfully should push
the season result DOWN. A result that improves is a red flag, not a win.
"""

from __future__ import annotations

from rehoboam.replay.engine import Matchday, run_season
from rehoboam.replay.state import ReplayPlayer, ReplayState

DAY = 86400.0
POS = ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 4 + ["Forward"] * 2 + ["Forward"]


class NoMarket:
    def available_before(self, at):
        return []


def _squad_of_12(basis: int = 10_000_000):
    """Twelve players, so one sale still leaves a legal eleven."""
    return {
        str(i): ReplayPlayer(
            id=str(i), position=POS[i], team_id=str(i), buy_price=basis, bought_at=0.0
        )
        for i in range(12)
    }


def _run(*, current_mv: int, profit_take_pct=None, loss_cut_pct=None, squad=None):
    state = ReplayState(budget=10_000_000, squad=squad or _squad_of_12())
    # Player "11" is the flip candidate; everyone else holds their basis.
    result = run_season(
        state=state,
        market=NoMarket(),
        matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: current_mv if pid == "11" else 10_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: pid,
        profit_take_pct=profit_take_pct,
        loss_cut_pct=loss_cut_pct,
    )
    return result, state


def test_a_player_up_twenty_percent_is_sold_for_profit():
    _result, state = _run(current_mv=12_000_000, profit_take_pct=15.0)

    assert "11" not in state.squad


def test_a_player_up_five_percent_is_held():
    """Below the take-profit threshold — churning him would just pay the
    ~11.7% buy-side premium again on the way back in."""
    _result, state = _run(current_mv=10_500_000, profit_take_pct=15.0)

    assert "11" in state.squad


def test_a_player_down_twenty_percent_is_cut():
    _result, state = _run(current_mv=8_000_000, loss_cut_pct=-15.0)

    assert "11" not in state.squad


def test_the_proceeds_of_a_profit_sale_reach_the_budget():
    """Instant sell returns full market value (REH-67)."""
    _result, state = _run(current_mv=12_000_000, profit_take_pct=15.0)

    assert state.budget == 10_000_000 + 12_000_000


def test_flips_never_break_the_starting_eleven():
    """A squad of exactly eleven has no spare player, so a profitable one must
    be held regardless — an empty slot costs -100, far more than the gain."""
    eleven = {
        str(i): ReplayPlayer(
            id=str(i), position=POS[i], team_id=str(i), buy_price=10_000_000, bought_at=0.0
        )
        for i in range(11)
    }

    _result, state = _run(current_mv=99_000_000, profit_take_pct=15.0, squad=eleven)

    assert len(state.squad) == 11


def test_flipping_is_off_by_default():
    """The shipped replay path must be unchanged until the run that enables it."""
    _result, state = _run(current_mv=99_000_000)

    assert "11" in state.squad
