"""Tests for the min-hold-period guard and locked-phase emergency squad fill.

These two guards exist because of a real production incident: the bot won
Niang at €2.0M at 13:24, then dead-weight-sold him at €0.57M at 20:00 the
same day (-71% in 7h). Around the same date, the squad was sitting at
9/11 with cash idle because the locked-phase exit short-circuits before
any buying logic ever runs.

We test the two helper layers (``_was_recently_bought`` + the emergency
squad-fill candidate filter) directly, without standing up a full
KickbaseAPI mock — those would dwarf the signal we actually care about.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(tmp_path, store_dsn, settings, monkeypatch):
    """An AutoTrader stitched together with a real BidLearner on a test database.

    The Kickbase API isn't touched in these tests — the helpers under test
    only read from settings + the learner. Building a full API mock would
    add noise without coverage.
    """
    monkeypatch.chdir(tmp_path)  # isolate any incidental cwd file writes
    api = SimpleNamespace()
    t = AutoTrader(api=api, settings=settings, dry_run=True)
    t.learner = BidLearner(dsn=store_dsn)
    return t


def _player(pid: str, position: str = "Forward", price: int = 1_000_000):
    return SimpleNamespace(
        id=pid,
        first_name="X",
        last_name=f"P{pid}",
        position=position,
        price=price,
        market_value=price,
        average_points=10.0,
        status=0,
    )


# ---------------------------------------------------------------------------
# Min-hold guard
# ---------------------------------------------------------------------------


class TestMinHoldGuard:
    def test_no_purchase_record_does_not_block(self, trader):
        # Player we never tracked buying — guard can't tell, so don't block.
        held_too_briefly, hours = trader._was_recently_bought("never-bought")
        assert held_too_briefly is False
        assert hours is None

    def test_recently_bought_blocks_sell(self, trader):
        trader.learner.add_tracked_purchase(
            player_id="12333",
            player_name="Niang",
            buy_price=2_000_000,
            buy_date=time.time() - 3 * 3600,  # 3h ago
        )
        held_too_briefly, hours = trader._was_recently_bought("12333")
        assert held_too_briefly is True
        assert hours is not None
        assert 2.5 < hours < 3.5

    def test_old_purchase_does_not_block(self, trader):
        trader.learner.add_tracked_purchase(
            player_id="12333",
            player_name="Niang",
            buy_price=2_000_000,
            buy_date=time.time() - 5 * 86400,  # 5 days
        )
        held_too_briefly, hours = trader._was_recently_bought("12333")
        assert held_too_briefly is False
        assert hours is not None
        assert hours > 24

    def test_min_hold_setting_drives_threshold(self, trader, settings):
        # Drop the min-hold to 1h and verify the same 3h-old purchase no
        # longer blocks. The setting is the source of truth, not a
        # constant baked into the helper.
        settings.min_hold_hours_before_sell = 1.0
        trader.learner.add_tracked_purchase(
            player_id="12333",
            player_name="Niang",
            buy_price=2_000_000,
            buy_date=time.time() - 3 * 3600,
        )
        held_too_briefly, _ = trader._was_recently_bought("12333")
        assert held_too_briefly is False


# ---------------------------------------------------------------------------
# Wash-trade guard wiring (helper)
# ---------------------------------------------------------------------------


class TestWashTradeHelper:
    def test_clean_player_passes(self, trader):
        assert trader._is_wash_trade("12333") is False

    def test_recent_sell_blocks(self, trader):
        trader.learner.record_recent_sell(
            player_id="12333",
            player_name="Niang",
            sold_price=572_468,
            sold_at=time.time() - 3600,  # 1h ago
        )
        assert trader._is_wash_trade("12333") is True

    def test_old_sell_does_not_block(self, trader, settings):
        settings.wash_trade_block_hours = 24.0
        trader.learner.record_recent_sell(
            player_id="12333",
            player_name="Niang",
            sold_price=572_468,
            sold_at=time.time() - 48 * 3600,  # 2 days ago, window is 1 day
        )
        assert trader._is_wash_trade("12333") is False


# ---------------------------------------------------------------------------
# Emergency squad fill — at locked phase + squad < 11
# ---------------------------------------------------------------------------


class _StubExecution:
    """Records every buy/sell call without hitting the API."""

    def __init__(self):
        self.calls: list = []

    def buy(
        self,
        league,
        player,
        price,
        reason,
        sell_plan_player_ids=None,
        *,
        current_budget=None,
        days_until_match=None,
        gate=None,
    ):
        self.calls.append(("buy", player.id, price, reason))
        return SimpleNamespace(
            success=True,
            player_name=player.last_name,
            action="BUY",
            price=price,
            reason=reason,
            timestamp=time.time(),
            error=None,
        )

    def instant_sell(self, league, player, reason):
        self.calls.append(("sell", player.id, player.market_value, reason))
        return SimpleNamespace(
            success=True,
            player_name=player.last_name,
            action="SELL",
            price=player.market_value,
            reason=reason,
            timestamp=time.time(),
            error=None,
        )


def _ctx(buy_recs, current_budget, my_bid_amounts=None, days_until_match=None):
    """Build the slimmest EPSessionContext-shaped object _run_emergency_squad_fill needs.

    ``days_until_match`` defaults to None so the REH-11 safety guard inside
    ExecutionService.buy() treats the schedule as unknown and skips the
    block (these tests aren't exercising the guard, just the emergency
    fill logic).
    """
    return SimpleNamespace(
        ep_result={
            "buy_recs": buy_recs,
            "trade_pairs": [],
            "squad_scores": [],
            # REH-114: the fill now pre-flights `safety_gate` before proposing,
            # and `known_player_ids` is a security boundary — a candidate absent
            # from the live market is refused. The old `_StubExecution` ignored
            # the gate it was handed, so this fixture gap stayed invisible.
            "market_players": {r.player.id: r.player for r in buy_recs},
        },
        my_bid_amounts=my_bid_amounts or {},
        my_bids=[],
        squad=[],
        team_value=100_000_000,
        current_budget=current_budget,
        matchday_phase=SimpleNamespace(days_until_match=days_until_match, phase="moderate"),
    )


def _rec(pid, position, price, ep_gain=10.0):
    return SimpleNamespace(
        player=_player(pid, position=position, price=price),
        recommended_bid=price,
        marginal_ep_gain=ep_gain,
        sell_plan=None,
    )


class TestEmergencySquadFill:
    def test_fills_to_eleven_when_short(self, trader):
        trader.execution = _StubExecution()

        # Squad has 9 players (missing 1 GK + 1 forward = formation broken)
        squad = (
            [_player(f"def{i}", "Defender") for i in range(4)]
            + [_player(f"mid{i}", "Midfielder") for i in range(3)]
            + [_player("fwd0", "Forward")]
            + [_player("gk0", "Goalkeeper")]
        )
        assert len(squad) == 9

        buy_recs = [
            _rec("forward1", "Forward", price=2_000_000, ep_gain=15.0),
            _rec("forward2", "Forward", price=1_500_000, ep_gain=12.0),
            _rec("def_extra", "Defender", price=900_000, ep_gain=18.0),
        ]
        ctx = _ctx(buy_recs, current_budget=10_000_000)

        results = trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=2
        )

        assert sum(1 for r in results if r.success) == 2
        # Both slots are bought from affordable, non-active-bid candidates
        bought_ids = [pid for kind, pid, *_ in trader.execution.calls if kind == "buy"]
        assert len(bought_ids) == 2

    def test_skips_wash_trade_blocked_candidates(self, trader):
        trader.execution = _StubExecution()
        # One short: GK 1, DEF 4, MID 4, FW 1 (10 players) -- fieldability
        # purchases == 1, and a Forward signing closes it (4-4-2).
        squad = (
            [_player("gk0", "Goalkeeper")]
            + [_player(f"d{i}", "Defender") for i in range(4)]
            + [_player(f"m{i}", "Midfielder") for i in range(4)]
            + [_player("fwd0", "Forward")]
        )

        trader.learner.record_recent_sell(
            player_id="forward1",
            player_name="P forward1",
            sold_price=2_000_000,
            sold_at=time.time() - 3600,
        )

        buy_recs = [
            _rec("forward1", "Forward", price=2_000_000, ep_gain=20.0),  # blocked
            _rec("forward2", "Forward", price=1_500_000, ep_gain=10.0),
        ]
        ctx = _ctx(buy_recs, current_budget=5_000_000)

        results = trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
        )

        bought_ids = [pid for kind, pid, *_ in trader.execution.calls if kind == "buy"]
        assert bought_ids == ["forward2"]
        assert sum(1 for r in results if r.success) == 1

    def test_skips_already_bid_candidates(self, trader):
        trader.execution = _StubExecution()
        # One short: GK 1, DEF 4, MID 4, FW 1 (10 players) -- fieldability
        # purchases == 1, and a Forward signing closes it (4-4-2).
        squad = (
            [_player("gk0", "Goalkeeper")]
            + [_player(f"d{i}", "Defender") for i in range(4)]
            + [_player(f"m{i}", "Midfielder") for i in range(4)]
            + [_player("fwd0", "Forward")]
        )

        buy_recs = [
            _rec("forward1", "Forward", price=2_000_000, ep_gain=20.0),
            _rec("forward2", "Forward", price=1_500_000, ep_gain=10.0),
        ]
        ctx = _ctx(
            buy_recs,
            current_budget=5_000_000,
            my_bid_amounts={"forward1": 2_000_000},  # already in flight
        )

        trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
        )

        bought_ids = [pid for kind, pid, *_ in trader.execution.calls if kind == "buy"]
        assert bought_ids == ["forward2"]

    def test_skips_unaffordable_candidates(self, trader):
        trader.execution = _StubExecution()
        # Two short: GK 1, DEF 4, MID 3, FW 1 (9 players) -- fieldability
        # purchases == 2, and a single Forward signing closes one of them.
        squad = (
            [_player("gk0", "Goalkeeper")]
            + [_player(f"d{i}", "Defender") for i in range(4)]
            + [_player(f"m{i}", "Midfielder") for i in range(3)]
            + [_player("fwd0", "Forward")]
        )

        buy_recs = [
            _rec("forward1", "Forward", price=20_000_000, ep_gain=30.0),  # too pricey
            _rec("forward2", "Forward", price=1_500_000, ep_gain=10.0),
        ]
        ctx = _ctx(buy_recs, current_budget=2_000_000)

        trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=2
        )

        bought_ids = [pid for kind, pid, *_ in trader.execution.calls if kind == "buy"]
        assert bought_ids == ["forward2"]

    def test_prioritises_gap_positions_over_raw_ep(self, trader):
        trader.execution = _StubExecution()
        # One short with a saturated outfield: DEF 4, MID 4, FW 2, no GK
        # (10 players) -- fieldability.purchases == 1, positions ==
        # {Goalkeeper}. Only a goalkeeper closes it; a defender (any
        # outfield position, already at or above its formation ceiling)
        # closes nothing.
        squad = (
            [_player(f"def{i}", "Defender") for i in range(4)]
            + [_player(f"mid{i}", "Midfielder") for i in range(4)]
            + [_player(f"fwd{i}", "Forward") for i in range(2)]
        )
        assert len(squad) == 10

        buy_recs = [
            # Higher-EP defender, but the position is saturated and closes
            # nothing.
            _rec("def_top", "Defender", price=1_000_000, ep_gain=25.0),
            # Lower-EP goalkeeper, but it is the only position that closes
            # the gap.
            _rec("gk_gap", "Goalkeeper", price=1_000_000, ep_gain=12.0),
        ]
        ctx = _ctx(buy_recs, current_budget=5_000_000)

        trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
        )

        # The gap-filling goalkeeper is bought; the saturated-position
        # defender closes nothing and must never be bought at all.
        bought_ids = [pid for kind, pid, *_ in trader.execution.calls if kind == "buy"]
        assert bought_ids == ["gk_gap"]

    def test_no_buys_when_no_affordable_clean_candidates(self, trader):
        trader.execution = _StubExecution()
        # One short: GK 1, DEF 4, MID 4, FW 1 (10 players) -- fieldability
        # purchases == 1, and a Forward signing closes it (4-4-2). A squad of
        # ten defenders would be short a goalkeeper too and never describes a
        # real board.
        squad = (
            [_player("gk0", "Goalkeeper")]
            + [_player(f"d{i}", "Defender") for i in range(4)]
            + [_player(f"m{i}", "Midfielder") for i in range(4)]
            + [_player("fwd0", "Forward")]
        )

        buy_recs = [
            _rec("forward1", "Forward", price=20_000_000, ep_gain=30.0),  # too pricey
        ]
        ctx = _ctx(buy_recs, current_budget=2_000_000)

        results = trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
        )

        assert results == []
        assert trader.execution.calls == []


def _ten_short_one_forward(team_ids=None):
    """GK 1, DEF 4, MID 4, FW 1 -- ten players, one short of a legal eleven;
    a second forward closes it (4-4-2)."""
    squad = (
        [_player("gk0", "Goalkeeper")]
        + [_player(f"d{i}", "Defender") for i in range(4)]
        + [_player(f"m{i}", "Midfielder") for i in range(4)]
        + [_player("fwd0", "Forward")]
    )
    for player, team_id in zip(squad, team_ids or [f"club-{p.id}" for p in squad]):
        player.team_id = team_id
    return squad


def _club_rec(pid, price, ep_gain, team_id):
    rec = _rec(pid, "Forward", price, ep_gain=ep_gain)
    rec.player.team_id = team_id
    return rec


class TestTheFillIgnoresProfitRules:
    """On 2026-09-15 the fill picked Baack for the empty slot and then refused
    him because his market value was down 40% in 7 days — a rule written for
    profit buys. The slot stayed empty at -100. The same board listed Henrichs
    first, with three Leipzig players already held, so the gate would have
    refused him and the basket's pick was wasted.

    Ported from `test_emergency_fill_approval.py`, which asserted the same
    behaviour through the proposal flow the fill no longer uses.
    """

    def test_a_falling_player_is_still_bought_in_an_emergency(self, trader):
        """The falling-price floor lives in `_propose_buy`. The fill buys
        through `execution`, so no trend — however bad — can empty the slot."""
        trader.execution = _StubExecution()
        falling = MagicMock()
        falling.return_value.trend_service.get_trend.return_value.trend_7d_pct = -40.3
        with patch("rehoboam.trader.Trader", falling):
            trader._run_emergency_squad_fill(
                league=SimpleNamespace(id="L"),
                ctx=_ctx([_club_rec("f1", 5_000_000, 60.0, "club-f1")], 50_000_000),
                fresh_squad=_ten_short_one_forward(),
                slots_short=1,
            )
        assert [c[1] for c in trader.execution.calls if c[0] == "buy"] == ["f1"]

    def test_a_club_at_its_limit_is_skipped_for_the_next_candidate(self, trader):
        trader.execution = _StubExecution()
        squad = _ten_short_one_forward(["7", "7", "7", "1", "2", "3", "4", "5", "6", "9"])
        recs = [
            _club_rec("blocked", 4_000_000, 90.0, "7"),
            _club_rec("ok", 5_000_000, 60.0, "8"),
        ]
        trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"),
            ctx=_ctx(recs, 50_000_000),
            fresh_squad=squad,
            slots_short=1,
        )
        assert [c[1] for c in trader.execution.calls if c[0] == "buy"] == ["ok"]

    def test_open_bids_count_toward_the_club_limit(self, trader):
        trader.execution = _StubExecution()
        squad = _ten_short_one_forward(["7", "7", "1", "2", "3", "4", "5", "6", "9", "10"])
        recs = [
            _club_rec("blocked", 4_000_000, 90.0, "7"),
            _club_rec("ok", 5_000_000, 60.0, "8"),
        ]
        ctx = _ctx(recs, 50_000_000)
        pending = _player("pending", "Forward", 1_000_000)
        pending.team_id = "7"
        ctx.my_bids = [pending]
        trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
        )
        assert [c[1] for c in trader.execution.calls if c[0] == "buy"] == ["ok"]
