"""REH-67: a buy-side control that changes only *who* gets bought.

The hold baseline (no buys at all) scored 15,100 against the shipped bot's
27,099, which rules out "the gain is only avoided churn" — but it cannot show
the scorer picks *well*, because it differs on two axes at once: whether to
trade and whom to pick.

This control fixes the trading tempo and varies only the chooser. Lineup and
sell decisions deliberately keep using EP, so any difference is attributable to
the buy side alone.
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


def _full_squad():
    return {str(i): ReplayPlayer(id=str(i), position=POS[i], team_id=str(i)) for i in range(11)}


def _run(*, buy_rank_fn=None, buy_quota=None):
    """Two candidates in direct conflict: 'cheap-star' is the better player,
    'pricey-dud' is the more valuable asset. EP prefers one, market value the
    other, so whichever is bought identifies the chooser that was used.
    """
    state = ReplayState(budget=100_000_000, squad=_full_squad())
    eps = {"cheap-star": 200.0, "pricey-dud": 12.0}
    mvs = {"cheap-star": 1_000_000, "pricey-dud": 40_000_000}

    return (
        run_season(
            state=state,
            market=FakeMarket(
                [
                    MarketListing(player_id="cheap-star", price=1_000_000, transfer_at=DAY),
                    MarketListing(player_id="pricey-dud", price=40_000_000, transfer_at=DAY),
                ]
            ),
            matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
            score_fn=lambda pid, at: eps.get(pid, 10.0),
            mv_fn=lambda pid, at: mvs.get(pid, 1_000_000),
            position_fn=lambda pid: "Forward",
            team_fn=lambda pid: pid,
            buy_rank_fn=buy_rank_fn,
            buy_quota=buy_quota,
        ),
        state,
    )


def test_market_value_control_buys_the_expensive_player_ep_would_reject():
    """The whole point of the control: rank by market value, not by EP."""
    mvs = {"cheap-star": 1_000_000, "pricey-dud": 40_000_000}

    _result, state = _run(buy_rank_fn=lambda pid, at: float(mvs.get(pid, 0)), buy_quota={1: 1})

    assert "pricey-dud" in state.squad
    assert "cheap-star" not in state.squad


def test_the_quota_caps_how_many_buys_a_matchday_may_make():
    """Trading tempo is held fixed so only the chooser varies."""
    mvs = {"cheap-star": 1_000_000, "pricey-dud": 40_000_000}

    _result, state = _run(buy_rank_fn=lambda pid, at: float(mvs.get(pid, 0)), buy_quota={1: 0})

    assert "pricey-dud" not in state.squad
    assert "cheap-star" not in state.squad


def test_without_a_control_the_ep_ranking_still_decides():
    """The control must be opt-in — the shipped path is unchanged."""
    _result, state = _run()

    assert "cheap-star" in state.squad


def test_quota_is_taken_from_the_reference_run_matchday_by_matchday():
    """Season-wide matching would let the control front-load its buys into the
    weeks that happened to be cheap. Tempo is matched per matchday."""
    from rehoboam.replay.driver import buy_quota_from
    from rehoboam.replay.engine import MatchdayOutcome, SeasonResult

    def _outcome(day, buys):
        return MatchdayOutcome(
            day_number=day,
            points_scored=0,
            lineup_ids=[],
            penalty=0,
            budget_at_kickoff=0,
            zeroed=False,
            squad_size=15,
            buys=buys,
            sells=0,
        )

    result = SeasonResult(outcomes=[_outcome(1, 3), _outcome(2, 0), _outcome(3, 1)])

    assert buy_quota_from(result) == {1: 3, 2: 0, 3: 1}
