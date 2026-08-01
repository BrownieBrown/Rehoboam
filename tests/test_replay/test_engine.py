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


def _matchday(day, kickoff, points):
    return Matchday(day_number=day, kickoff=kickoff, points=points)


def test_scores_the_points_of_the_fielded_eleven():
    state = ReplayState(budget=0, squad=_full_squad())
    mds = [_matchday(1, 10 * DAY, {str(i): 50.0 for i in range(11)})]
    result = run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    assert result.total_points == 550
    assert result.outcomes[0].penalty == 0


def test_negative_budget_at_kickoff_zeroes_the_matchday():
    state = ReplayState(budget=-1, squad=_full_squad())
    mds = [_matchday(1, 10 * DAY, {str(i): 50.0 for i in range(11)})]
    result = run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    assert result.outcomes[0].zeroed is True
    assert result.total_points == 0


def test_short_squad_incurs_the_empty_slot_penalty():
    squad = {str(i): ReplayPlayer(id=str(i), position=POS[i], team_id=str(i)) for i in range(9)}
    state = ReplayState(budget=0, squad=squad)
    mds = [_matchday(1, 10 * DAY, {str(i): 50.0 for i in range(9)})]
    result = run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    assert result.outcomes[0].penalty == -200
    assert result.outcomes[0].points_scored == 9 * 50 - 200


def test_buys_a_clearly_better_player_when_affordable():
    state = ReplayState(budget=50_000_000, squad=_full_squad())
    market = FakeMarket([MarketListing(player_id="star", price=10_000_000, transfer_at=9 * DAY)])
    mds = [_matchday(1, 10 * DAY, {**{str(i): 10.0 for i in range(11)}, "star": 100.0})]
    result = run_season(
        state=state,
        market=market,
        matchdays=mds,
        score_fn=lambda pid, at: 200.0 if pid == "star" else 1.0,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "star-team" if pid == "star" else pid,
    )
    assert "star" in result.outcomes[0].lineup_ids
    assert result.outcomes[0].buys == 1


def test_does_not_buy_when_gain_is_below_threshold():
    state = ReplayState(budget=50_000_000, squad=_full_squad())
    market = FakeMarket([MarketListing(player_id="meh", price=10_000_000, transfer_at=9 * DAY)])
    mds = [_matchday(1, 10 * DAY, {str(i): 10.0 for i in range(11)})]
    result = run_season(
        state=state,
        market=market,
        matchdays=mds,
        score_fn=lambda pid, at: 10.0,  # identical to squad — no marginal gain
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "meh-team",
        min_ep_gain=5.0,
    )
    assert result.outcomes[0].buys == 0


def test_engine_never_reads_points_of_the_matchday_being_played():
    """Leak guard: score_fn must never be handed the matchday's own result."""
    seen = []
    state = ReplayState(budget=0, squad=_full_squad())
    mds = [_matchday(5, 10 * DAY, {str(i): 999.0 for i in range(11)})]

    def spy(pid, at):
        seen.append(at)
        return 10.0

    run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=spy,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    assert seen, "score_fn was never called"
    assert all(at < 10 * DAY for at in seen), "scored at or after kickoff — leak"
