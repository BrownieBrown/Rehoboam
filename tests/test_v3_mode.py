"""`trading_mode=v3` (ADR-0008..0013): v3 decides and executes; no legacy phase runs.

v3 repays its own debt before the kickoff (`flipbook.repay`) and fills its own
slots (`core.bids`), so the legacy debt recovery and emergency fill would fight
it. The league's Top-5 rule and the lineup still run. Tested at the call sites,
as `test_trading_mode` does for lineup_only.
"""

from __future__ import annotations

from unittest.mock import patch

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings
from tests.test_trading_mode import _legal_squad, _run


def test_v3_parses_from_env(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("TRADING_MODE", "v3")

    assert Settings().trading_mode == "v3"


def _run_v3(tmp_path, monkeypatch, **kwargs):
    with (
        patch.object(AutoTrader, "_run_v3_session", return_value=([], [])) as v3_session,
        patch.object(AutoTrader, "_run_debt_recovery", return_value=[]) as recovery,
    ):
        r = _run(_legal_squad(), "v3", tmp_path, monkeypatch, **kwargs)
    return r, v3_session, recovery


def test_v3_runs_its_session_and_no_legacy_trading_phase(tmp_path, monkeypatch):
    r, v3_session, recovery = _run_v3(tmp_path, monkeypatch)

    v3_session.assert_called_once()
    recovery.assert_not_called()
    for phase in (r.fill, r.sells, r.optimise, r.trades):
        phase.assert_not_called()
    r.top5.assert_called_once()  # the league's rule is not a trade
    r.lineup.assert_called_once()


def test_v3_still_runs_in_the_locked_window_where_it_repays(tmp_path, monkeypatch):
    r, v3_session, recovery = _run_v3(tmp_path, monkeypatch, phase="locked", days=0)

    v3_session.assert_called_once()
    recovery.assert_not_called()
    r.fill.assert_not_called()
    r.lineup.assert_called_once()


def test_the_v3_session_executes_the_decision_through_the_safety_gate(tmp_path, monkeypatch):
    """Runs `_run_v3_session` for real (dry run): the decision's orders reach the
    instant sale and the gated buy, and a +9.9% flip passes the solid ceiling."""
    from contextlib import contextmanager
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from rehoboam.desk.model import Offer, Player
    from rehoboam.kickbase_client import MarketPlayer
    from rehoboam.live import v3
    from rehoboam.sim.engine import Decision
    from rehoboam.sim.history import Listing
    from tests.test_trading_mode import _context, _player

    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("TRADING_MODE", "v3")
    monkeypatch.chdir(tmp_path)
    squad = [*_legal_squad(), _player("bench", "Midfielder")]
    riser = MarketPlayer(
        id="riser",
        first_name="R",
        last_name="Iser",
        position="Forward",
        team_id="9",
        team_name="T9",
        price=4_790_000,
        market_value=4_790_000,
        points=0,
        average_points=0.0,
        status=0,
    )

    class Api:
        user = SimpleNamespace(id="3616202")
        last_market_payload = {"it": [{"i": "riser", "mv": 4_790_000, "exs": 20 * 3600}]}

        def get_squad(self, league):
            return list(squad)

        def get_team_info(self, league):
            return {"budget": 1_000_000, "team_value": 12_000_000}

        def get_market(self, league):
            return [riser]

        def get_my_bids(self, league):
            return []

    now = datetime.now(timezone.utc)
    listing = Listing("riser", now, now + timedelta(hours=20), now, 4_790_000)
    decision = Decision(
        sells=(Player("bench", "Midfielder", 1_000_000, 5.0, 0.0),),
        bids=(Offer(Player("riser", "Forward", 4_790_000, 20.0, 0.36), 0.099),),
        books={"riser": "flip"},
        listings={"riser": listing},
        repaying=False,
    )
    trader = AutoTrader(api=Api(), settings=Settings(), dry_run=True)

    @contextmanager
    def no_store():
        yield None

    ctx = _context(squad)
    ctx.ep_result["market_players"] = {"riser": riser}
    with (
        patch.object(trader.learner, "connection", no_store),
        patch.object(v3, "books_from_store", return_value={}),
        patch.object(v3, "plan", return_value=(decision, None, now)),
        patch.object(v3, "describe", return_value=[]),
    ):
        sells, buys = trader._run_v3_session(SimpleNamespace(id="1", name="L"), ctx)

    assert [(r.action, r.success) for r in sells] == [("SELL", True)]
    assert [(r.action, r.success, r.price) for r in buys] == [("BUY", True, 5_264_210)]
    assert ctx.offers_placed == 1 and ctx.offers_refused == 0
