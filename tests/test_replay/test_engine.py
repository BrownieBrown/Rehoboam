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
    # The debt is far beyond what liquidating this squad could raise (11 players
    # at EUR 1,000,000 yields EUR 11,000,000 at the measured 1.00 instant-sell
    # rate -- see config.INSTANT_SELL_PCT, REH-67), so
    # the engine cannot trade its way out and eats the zero. A *recoverable*
    # deficit is covered by test_sells_below_eleven_to_avoid_a_zeroed_matchday.
    state = ReplayState(budget=-100_000_000, squad=_full_squad())
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


# ---------------------------------------------------------------------------
# Fix round 1: fieldability-safe sells, penalty-over-zero, fresh team value,
# and position-aware marginal gain.
# ---------------------------------------------------------------------------


def _named_squad(spec):
    """``spec`` is ``[(player_id, position), ...]``; each player gets own team."""
    return {pid: ReplayPlayer(id=pid, position=pos, team_id=pid) for pid, pos in spec}


# 1 GK, 5 DEF, 5 MID, 4 FW — a legal full squad of fifteen.
_FIFTEEN = (
    [("gk", "Goalkeeper")]
    + [(f"d{i}", "Defender") for i in range(1, 6)]
    + [(f"m{i}", "Midfielder") for i in range(1, 6)]
    + [(f"f{i}", "Forward") for i in range(1, 5)]
)


def _gk_is_cheapest(pid, at):
    """The goalkeeper scores lowest, so a naive min() would always sell them."""
    if pid == "gk":
        return 0.0
    if pid == "d1":
        return 1.0
    return 50.0


def test_make_room_sale_never_strands_the_squad_without_a_goalkeeper():
    """At 15/15 the sale victim must leave a squad that can still field eleven."""
    state = ReplayState(budget=50_000_000, squad=_named_squad(_FIFTEEN))
    market = FakeMarket([MarketListing(player_id="star", price=1_000_000, transfer_at=9 * DAY)])
    mds = [_matchday(1, 10 * DAY, {"star": 100.0})]
    result = run_season(
        state=state,
        market=market,
        matchdays=mds,
        score_fn=lambda pid, at: 200.0 if pid == "star" else _gk_is_cheapest(pid, at),
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "star-team",
    )
    assert "gk" in state.squad, "sold the only goalkeeper to make room"
    assert "d1" not in state.squad, "should have sold the cheapest legal victim"
    assert result.outcomes[0].buys == 1
    assert result.outcomes[0].sells == 1
    assert len(result.outcomes[0].lineup_ids) == 11


def test_budget_recovery_prefers_a_sale_that_keeps_the_eleven_legal():
    """Selling to clear a deficit must not gratuitously break the formation."""
    spec = (
        [("gk", "Goalkeeper")]
        + [(f"d{i}", "Defender") for i in range(1, 5)]
        + [(f"m{i}", "Midfielder") for i in range(1, 6)]
        + [(f"f{i}", "Forward") for i in range(1, 3)]
    )
    state = ReplayState(budget=-1_000_000, squad=_named_squad(spec))
    mds = [_matchday(1, 10 * DAY, {pid: 50.0 for pid, _ in spec})]
    result = run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=_gk_is_cheapest,
        mv_fn=lambda pid, at: 2_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: pid,
    )
    assert "gk" in state.squad, "sold the only goalkeeper to raise cash"
    assert "d1" not in state.squad, "should have sold the cheapest legal victim"
    assert result.outcomes[0].sells == 1
    assert result.outcomes[0].zeroed is False
    assert len(result.outcomes[0].lineup_ids) == 11


def test_sells_below_eleven_to_avoid_a_zeroed_matchday():
    """A -100 empty-slot penalty beats a zero, so sell down past eleven."""
    state = ReplayState(budget=-1_000_000, squad=_full_squad())
    mds = [_matchday(1, 10 * DAY, {str(i): 50.0 for i in range(11)})]
    result = run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=lambda pid, at: 1.0 if pid == "10" else 10.0,
        mv_fn=lambda pid, at: 2_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    outcome = result.outcomes[0]
    assert outcome.zeroed is False, "took a zero rather than selling below eleven"
    assert outcome.sells == 1
    assert outcome.squad_size == 10
    assert outcome.penalty == -100
    assert outcome.points_scored == 10 * 50 - 100


def test_keeps_the_squad_when_no_sale_could_clear_the_debt():
    """A zero with the squad intact beats a zero with the squad liquidated."""
    state = ReplayState(budget=-100_000_000, squad=_full_squad())
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
    assert result.outcomes[0].sells == 0, "liquidated a squad it could not save"
    assert result.outcomes[0].squad_size == 11
    assert result.outcomes[0].zeroed is True


def test_credit_line_is_rechecked_against_team_value_after_the_sale():
    """The credit floor is 70% of *post-sale* team value, not the stale one.

    Fifteen players at EUR 1,000,000 give a EUR -10,500,000 floor; after one
    sale, fourteen players give EUR -9,800,000. Budget after the sale is
    EUR 950,000, so a EUR 11,000,000 price lands at EUR -10,050,000 — legal
    under the stale floor, illegal under the real one.
    """
    state = ReplayState(budget=0, squad=_named_squad(_FIFTEEN))
    market = FakeMarket([MarketListing(player_id="star", price=11_000_000, transfer_at=9 * DAY)])
    mds = [_matchday(1, 10 * DAY, {"star": 100.0})]
    result = run_season(
        state=state,
        market=market,
        matchdays=mds,
        score_fn=lambda pid, at: 200.0 if pid == "star" else _gk_is_cheapest(pid, at),
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "star-team",
    )
    assert result.outcomes[0].buys == 0, "bought past the real credit line"
    assert "star" not in state.squad


def test_rejects_a_candidate_who_cannot_improve_the_best_eleven():
    """Marginal gain is measured against the best legal eleven, not the bench.

    The squad's eleven starters all score 100; three bench players score 10.
    A candidate on 50 beats the bench by 40 but cannot displace any starter,
    so their true marginal gain is zero and the buy must be refused.
    """
    spec = (
        [("gk", "Goalkeeper")]
        + [(f"d{i}", "Defender") for i in range(1, 6)]
        + [(f"m{i}", "Midfielder") for i in range(1, 6)]
        + [(f"f{i}", "Forward") for i in range(1, 4)]
    )
    bench = {"d5", "m5", "f3"}
    state = ReplayState(budget=50_000_000, squad=_named_squad(spec))
    market = FakeMarket([MarketListing(player_id="meh", price=1_000_000, transfer_at=9 * DAY)])
    mds = [_matchday(1, 10 * DAY, {pid: 50.0 for pid, _ in spec})]
    result = run_season(
        state=state,
        market=market,
        matchdays=mds,
        score_fn=lambda pid, at: 50.0 if pid == "meh" else (10.0 if pid in bench else 100.0),
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Midfielder",
        team_fn=lambda pid: "meh-team",
        min_ep_gain=5.0,
    )
    assert result.outcomes[0].buys == 0, "bought a player who cannot reach the eleven"
    assert result.outcomes[0].sells == 0


def test_budget_never_goes_negative_at_kickoff_with_an_expensive_market():
    """Solvency gate: the engine may only spend cash it holds at decision time.

    Every decision is taken one hour before kickoff, so there is no window in
    which to recover — the credit line is a mid-week facility this engine does
    not model. Gated on the credit line alone, the engine leveraged to many
    times its cash (a squad worth EUR 220,000,000 licenses a EUR -154,000,000
    floor) and then liquidated the squad at market value to get back to zero,
    losing the buy-side premium on every forced round trip. Here it must simply
    decline: it
    can afford the EUR 3,000,000 listing and nothing else.
    """
    squad = {str(i): ReplayPlayer(id=str(i), position=POS[i], team_id=str(i)) for i in range(11)}
    state = ReplayState(budget=5_000_000, squad=squad)
    listings = [MarketListing(player_id="cheap", price=3_000_000, transfer_at=0.0)] + [
        MarketListing(player_id=f"star{i}", price=30_000_000, transfer_at=0.0) for i in range(11)
    ]
    mds = [_matchday(d, d * DAY, {}) for d in range(1, 11)]
    result = run_season(
        state=state,
        market=FakeMarket(listings),
        matchdays=mds,
        score_fn=lambda pid, at: 1.0 if pid.isdigit() else 200.0,
        mv_fn=lambda pid, at: 20_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: pid,
    )
    assert all(o.budget_at_kickoff >= 0 for o in result.outcomes), (
        "spent money it did not hold: "
        f"{[o.budget_at_kickoff for o in result.outcomes if o.budget_at_kickoff < 0]}"
    )
    assert not any(o.zeroed for o in result.outcomes)
    assert all(len(o.lineup_ids) == 11 for o in result.outcomes), "squad collapsed below eleven"
    assert sum(o.buys for o in result.outcomes) == 1, "bought something it could not afford"
    assert sum(o.sells for o in result.outcomes) == 0, "forced into a liquidation"
