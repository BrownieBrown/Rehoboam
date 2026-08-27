"""Tests for REH-103: a won purchase must never be invisible to the learning loop.

Raum (EUR 40,717,295, 2026-08-26) — the season's largest acquisition — entered
the squad with no `pending_bids` row, so `resolve_auctions` never saw him and
neither `auction_outcomes` nor `tracked_purchases` recorded anything. The
consequence is not cosmetic: `enable_profit_sells` evaluates a squad player
against its cost basis, so a missing row silently disables profit-taking and
loss-cutting for that player forever. On 2026-08-27 only 2 of 12 squad players
had a basis.

The fix reconciles squad membership against `tracked_purchases` every session,
recovering the real price from `manager_transfers` (the activity-feed mirror,
which did record Raum) rather than depending on the bidding path having
remembered.
"""

import sqlite3

import pytest

from rehoboam.bid_learner import BidLearner
from rehoboam.learning.tracker import LearningTracker

LEAGUE = "1933872"
US = "3616202"
THEM = "1907519"

BUY = 1
SELL = 2


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


@pytest.fixture
def tracker(learner):
    return LearningTracker(learner)


def _transfer(player_id, name, dt, typ, price, manager_id=US):
    return {
        "league_id": LEAGUE,
        "manager_id": manager_id,
        "transfer_dt": dt,
        "player_id": player_id,
        "player_name": name,
        "transfer_type": typ,
        "transfer_price": price,
    }


class FakePlayer:
    """Minimal stand-in for api.Player — only what reconciliation reads."""

    def __init__(self, player_id, first_name, last_name):
        self.id = player_id
        self.first_name = first_name
        self.last_name = last_name


class TestLastPurchasePrice:
    """`BidLearner.get_last_purchase_price` — recover a real cost basis."""

    def test_returns_price_of_the_most_recent_buy(self, learner):
        learner.record_manager_transfers(
            [
                _transfer("p1", "Raum", "2026-08-26T15:28:32Z", BUY, 40_717_295),
            ]
        )

        assert learner.get_last_purchase_price(US, "p1") == 40_717_295

    def test_a_sale_after_the_buy_invalidates_that_basis(self, learner):
        """The Da Costa case — the reason 'most recent buy' is not enough.

        Bought 2026-05-03 for EUR 11,908,528 and SOLD 2026-05-11. The player
        now in the squad is an untracked re-acquisition at an unknown price;
        reporting the May figure would attach a stale basis to a different
        holding and drive profit/loss decisions off it.
        """
        learner.record_manager_transfers(
            [
                _transfer("p2", "Da Costa", "2026-05-03T06:51:08Z", BUY, 11_908_528),
                _transfer("p2", "Da Costa", "2026-05-11T20:00:01Z", SELL, 11_099_669),
            ]
        )

        assert learner.get_last_purchase_price(US, "p2") is None

    def test_a_rebuy_after_the_sale_is_the_live_basis(self, learner):
        learner.record_manager_transfers(
            [
                _transfer("p2", "Da Costa", "2026-05-03T06:51:08Z", BUY, 11_908_528),
                _transfer("p2", "Da Costa", "2026-05-11T20:00:01Z", SELL, 11_099_669),
                _transfer("p2", "Da Costa", "2026-08-01T09:00:00Z", BUY, 9_308_429),
            ]
        )

        assert learner.get_last_purchase_price(US, "p2") == 9_308_429

    def test_another_managers_purchase_is_not_ours(self, learner):
        learner.record_manager_transfers(
            [
                _transfer("p3", "Kimmich", "2026-08-25T02:16:46Z", BUY, 65_000_065, THEM),
            ]
        )

        assert learner.get_last_purchase_price(US, "p3") is None

    def test_no_transfer_history_yields_no_basis(self, learner):
        assert learner.get_last_purchase_price(US, "unknown-player") is None


class TestReconcileSquadCostBasis:
    """`LearningTracker.reconcile_squad_cost_basis` — close the recording gap."""

    def test_records_a_basis_for_an_untracked_squad_player(self, learner, tracker):
        """Raum, exactly: in the squad, in the transfer feed, tracked nowhere."""
        learner.record_manager_transfers(
            [_transfer("p1", "Raum", "2026-08-26T15:28:32Z", BUY, 40_717_295)]
        )
        squad = [FakePlayer("p1", "David", "Raum")]

        tracker.reconcile_squad_cost_basis(squad, manager_id=US)

        tracked = learner.get_tracked_purchase("p1")
        assert tracked is not None
        assert tracked["buy_price"] == 40_717_295

    def test_recovered_rows_are_marked_as_backfilled(self, learner, tracker):
        """`source` must distinguish a recovered basis from one the bidder wrote."""
        learner.record_manager_transfers(
            [_transfer("p1", "Raum", "2026-08-26T15:28:32Z", BUY, 40_717_295)]
        )

        tracker.reconcile_squad_cost_basis([FakePlayer("p1", "David", "Raum")], manager_id=US)

        assert learner.get_tracked_purchase("p1")["source"] == "transfer_feed"

    def test_an_existing_basis_is_never_overwritten(self, learner, tracker):
        """The bidding path's own record is authoritative — it knows the bid."""
        learner.add_tracked_purchase(
            player_id="p1",
            player_name="Raum",
            buy_price=38_000_000,
            buy_date=1_000.0,
            source="real",
        )
        learner.record_manager_transfers(
            [_transfer("p1", "Raum", "2026-08-26T15:28:32Z", BUY, 40_717_295)]
        )

        tracker.reconcile_squad_cost_basis([FakePlayer("p1", "David", "Raum")], manager_id=US)

        tracked = learner.get_tracked_purchase("p1")
        assert tracked["buy_price"] == 38_000_000
        assert tracked["source"] == "real"

    def test_unrecoverable_basis_writes_no_row(self, learner, tracker):
        """No fabricated price.

        A sentinel price would feed straight into profit-sell arithmetic:
        zero divides, and any invented figure silently authorises a real trade.
        Absence is reported instead — see the return value.
        """
        squad = [FakePlayer("p9", "Kevin", "Stark")]

        tracker.reconcile_squad_cost_basis(squad, manager_id=US)

        assert learner.get_tracked_purchase("p9") is None

    def test_reports_squad_players_left_without_a_basis(self, learner, tracker):
        """The invariant: the caller must be able to see the remaining gap."""
        learner.record_manager_transfers(
            [_transfer("p1", "Raum", "2026-08-26T15:28:32Z", BUY, 40_717_295)]
        )
        squad = [
            FakePlayer("p1", "David", "Raum"),
            FakePlayer("p9", "Kevin", "Stark"),
            FakePlayer("p8", "Jordan", "Chandler"),
        ]

        result = tracker.reconcile_squad_cost_basis(squad, manager_id=US)

        assert result.recovered == 1
        assert set(result.still_missing) == {"p9", "p8"}

    def test_a_fully_tracked_squad_reports_no_gap(self, learner, tracker):
        learner.add_tracked_purchase(
            player_id="p1",
            player_name="Raum",
            buy_price=40_717_295,
            buy_date=1_000.0,
            source="real",
        )

        result = tracker.reconcile_squad_cost_basis(
            [FakePlayer("p1", "David", "Raum")], manager_id=US
        )

        assert result.recovered == 0
        assert result.still_missing == []

    def test_buy_date_comes_from_the_transfer_not_from_now(self, learner, tracker):
        """Hold-period maths (flip_outcomes.hold_days) reads this date."""
        learner.record_manager_transfers(
            [_transfer("p1", "Raum", "2026-08-26T15:28:32Z", BUY, 40_717_295)]
        )

        tracker.reconcile_squad_cost_basis([FakePlayer("p1", "David", "Raum")], manager_id=US)

        # 2026-08-26T15:28:32Z
        assert learner.get_tracked_purchase("p1")["buy_date"] == pytest.approx(
            1_787_758_112.0, abs=1.0
        )

    def test_reconciliation_is_scoped_to_the_squad(self, learner, tracker):
        """A player we once owned but no longer hold must not be re-tracked."""
        learner.record_manager_transfers(
            [_transfer("gone", "Sabitzer", "2026-08-01T09:00:00Z", BUY, 7_951_576)]
        )

        tracker.reconcile_squad_cost_basis([], manager_id=US)

        assert learner.get_tracked_purchase("gone") is None

    def test_a_learning_failure_never_raises_into_the_session(self, learner, tracker):
        """Every tracker method is best-effort; a broken DB must not stop trading."""
        with sqlite3.connect(learner.db_path) as conn:
            conn.execute("DROP TABLE manager_transfers")
            conn.commit()

        result = tracker.reconcile_squad_cost_basis(
            [FakePlayer("p1", "David", "Raum")], manager_id=US
        )

        assert result.recovered == 0


class TestTheSessionActuallyCallsIt:
    """REH-103 is instrumentation. Shipping it unwired would repeat the bug.

    `auction_outcomes.winning_bid` sat NULL on every production row for a
    season because a writer existed and nothing called it (REH-86). The same
    trap applies here, so the call site is proved rather than assumed.
    """

    def test_step_one_recovers_cost_basis_for_the_live_squad(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import patch

        from rehoboam.auto_trader import AutoTrader
        from rehoboam.config import Settings

        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)

        class _Api:
            user = SimpleNamespace(id=US)

            def get_squad(self, league):
                return [FakePlayer("p1", "David", "Raum")]

            def get_my_bids(self, league):
                return []

            def get_team_info(self, league):
                return {"budget": 21_650_227, "teamValue": 145_278_078}

        trader = AutoTrader(api=_Api(), settings=Settings(), dry_run=True)
        trader.learner = BidLearner(db_path=tmp_path / "bid_learning.db")
        trader.tracker = LearningTracker(trader.learner)
        trader.learner.record_manager_transfers(
            [_transfer("p1", "Raum", "2026-08-26T15:28:32Z", BUY, 40_717_295)]
        )

        # Stop the session immediately after step 1 — this test is about the
        # call site, not the rest of the pipeline.
        with (
            patch.object(
                AutoTrader, "_build_session_context", side_effect=RuntimeError("stop after step 1")
            ),
            patch.object(AutoTrader, "_set_optimal_lineup", return_value=[]),
        ):
            trader.run_full_session(SimpleNamespace(id="L1", name="L1"))

        tracked = trader.learner.get_tracked_purchase("p1")
        assert tracked is not None, "step 1 never reconciled cost basis"
        assert tracked["buy_price"] == 40_717_295
