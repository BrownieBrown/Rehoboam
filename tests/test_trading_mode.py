"""`trading_mode=lineup_only`: lineups and the emergency fill, nothing else.

Decided 2026-09-11 (spec 2026-09-11 §5): while the data foundation is rebuilt
the bot must not sell or buy, except to make an eleven fieldable. Tested at
the call sites, because a mode that skips the wrong step is indistinguishable
from a quiet market.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from rehoboam.auto_trader import AutoTrader, AutoTradeResult, EPSessionContext, MatchdayPhase
from rehoboam.config import Settings
from rehoboam.kickbase_client import Player

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


def _player(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=50.0,
    )


def _legal_squad() -> list[Player]:
    return (
        [_player("gk", "Goalkeeper")]
        + [_player(f"d{i}", "Defender") for i in range(4)]
        + [_player(f"m{i}", "Midfielder") for i in range(4)]
        + [_player(f"f{i}", "Forward") for i in range(2)]
    )


def _short_squad() -> list[Player]:
    return (
        [_player("gk", "Goalkeeper")]
        + [_player(f"d{i}", "Defender") for i in range(6)]
        + [_player(f"m{i}", "Midfielder") for i in range(3)]
        + [_player("f0", "Forward")]
    )


class _Api:
    user = SimpleNamespace(id="3616202")

    def __init__(self, squad):
        self._squad = squad

    def get_squad(self, league):
        return list(self._squad)

    def get_my_bids(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": 5_868_658, "team_value": 149_641_186}


def _context(squad, phase="moderate", days=4) -> EPSessionContext:
    return EPSessionContext(
        ep_result={"squad_scores": [], "market_players": {}},
        matchday_phase=MatchdayPhase(
            days_until_match=days,
            phase=phase,
            max_trades=10,
            allow_flips=False,
            reason="t",
        ),
        my_bids=[],
        my_bid_amounts={},
        squad=list(squad),
        current_budget=5_868_658,
        team_value=149_641_186,
        flip_budget=0,
    )


class _SellSpy:
    """Stands in for `ExecutionService.instant_sell`, recording who was sold."""

    def __init__(self):
        self.sold: list[str] = []

    def __call__(self, league, player, reason):
        self.sold.append(player.id)
        return AutoTradeResult(
            success=True,
            player_name=player.last_name,
            action="SELL",
            price=1_000_000,
            reason=reason,
            timestamp=0.0,
        )


def _run(squad, mode, tmp_path, monkeypatch, phase="moderate", days=4, deferred_sell_ids=None):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.chdir(tmp_path)
    trader = AutoTrader(api=_Api(squad), settings=Settings(), dry_run=True)
    sells_executed = _SellSpy()
    if deferred_sell_ids is not None:
        # Step 1 hands the session the auctions it won and the sell plans that
        # were deferred behind them; the mode decides whether they execute.
        trader.tracker.resolve_auctions = lambda **kwargs: list(deferred_sell_ids)
        trader.execution.instant_sell = sells_executed
    ctx = _context(squad, phase=phase, days=days)
    with (
        patch.object(AutoTrader, "_build_session_context", return_value=ctx),
        patch.object(AutoTrader, "_run_emergency_squad_fill", return_value=[]) as fill,
        patch.object(AutoTrader, "run_profit_sell_phase", return_value=[]) as sells,
        patch.object(AutoTrader, "optimize_and_execute_squad", return_value=[]) as optimise,
        patch.object(AutoTrader, "run_unified_trade_phase", return_value=[]) as trades,
        patch.object(AutoTrader, "_evaluate_open_bids", return_value=None) as bid_eval,
        patch.object(AutoTrader, "_set_optimal_lineup", return_value=[]) as lineup,
        patch.object(AutoTrader, "_settle_top5_obligation", return_value=None) as top5,
    ):
        session = trader.run_full_session(LEAGUE)
    return SimpleNamespace(
        session=session,
        fill=fill,
        sells=sells,
        optimise=optimise,
        trades=trades,
        bid_eval=bid_eval,
        lineup=lineup,
        top5=top5,
        sells_executed=sells_executed,
    )


class TestSettings:
    def test_default_is_full(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.delenv("TRADING_MODE", raising=False)
        assert Settings().trading_mode == "full"

    def test_lineup_only_parses_from_env(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.setenv("TRADING_MODE", "lineup_only")
        assert Settings().trading_mode == "lineup_only"

    def test_anything_else_is_rejected(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.setenv("TRADING_MODE", "observe")
        with pytest.raises(ValidationError):
            Settings()


class TestLineupOnlySkipsEveryTradingStep:
    def test_no_sell_or_buy_phase_runs(self, tmp_path, monkeypatch):
        r = _run(_legal_squad(), "lineup_only", tmp_path, monkeypatch)
        assert not r.sells.called
        assert not r.optimise.called
        assert not r.trades.called
        assert not r.bid_eval.called
        assert r.lineup.call_count == 1

    def test_the_emergency_fill_still_runs_on_the_last_day(self, tmp_path, monkeypatch):
        r = _run(_short_squad(), "lineup_only", tmp_path, monkeypatch, phase="locked", days=1)
        assert r.fill.called
        assert r.fill.call_args.args[3] == 1
        assert r.lineup.call_count == 1

    def test_the_emergency_fill_waits_while_kickoff_is_days_away(self, tmp_path, monkeypatch):
        # 2026-09-22: ten players, seventeen days to kickoff, and the fill bought
        # a falling non-starter at +overbid. The last day is the emergency.
        r = _run(_short_squad(), "lineup_only", tmp_path, monkeypatch, days=4)
        assert not r.fill.called
        assert r.lineup.call_count == 1

    def test_locked_phase_still_exits_after_the_lineup(self, tmp_path, monkeypatch):
        r = _run(_legal_squad(), "lineup_only", tmp_path, monkeypatch, phase="locked", days=1)
        assert not r.trades.called
        assert r.lineup.call_count == 1

    def test_the_top5_obligation_is_still_settled(self, tmp_path, monkeypatch):
        r = _run(_legal_squad(), "lineup_only", tmp_path, monkeypatch)
        assert r.top5.called


class TestTheDeferredSellGate:
    """A won auction can leave a sell plan queued behind it (buy first, sell
    after). That sell is a trade, so `lineup_only` must not execute it — and
    `full` must, or the squad stays oversized with the budget still spent."""

    @staticmethod
    def _squad_with_the_sell_target() -> list[Player]:
        squad = _legal_squad()
        squad[1] = _player("x", "Defender")  # GK 1, DEF 4, MID 4, FW 2
        return squad

    def test_lineup_only_skips_it_and_says_so(self, tmp_path, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="rehoboam.auto_trader"):
            r = _run(
                self._squad_with_the_sell_target(),
                "lineup_only",
                tmp_path,
                monkeypatch,
                deferred_sell_ids=["x"],
            )
        assert r.sells_executed.sold == []
        assert any("deferred sell plans skipped n=1" in m for m in caplog.messages)

    def test_full_mode_executes_it(self, tmp_path, monkeypatch):
        r = _run(
            self._squad_with_the_sell_target(),
            "full",
            tmp_path,
            monkeypatch,
            deferred_sell_ids=["x"],
        )
        assert r.sells_executed.sold == ["x"]


class TestFullModeIsUnchanged:
    def test_trading_steps_run(self, tmp_path, monkeypatch):
        r = _run(_legal_squad(), "full", tmp_path, monkeypatch)
        assert r.sells.called
        assert r.optimise.called
        assert r.trades.called
        assert r.lineup.call_count == 1


class TestTheModeIsInTheLogs:
    def test_session_start_and_end_carry_the_mode(self, tmp_path, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="rehoboam.auto_trader"):
            _run(_legal_squad(), "lineup_only", tmp_path, monkeypatch)
        starts = [m for m in caplog.messages if m.startswith("session-start")]
        ends = [m for m in caplog.messages if m.startswith("session-end")]
        assert starts and "mode=lineup_only" in starts[0]
        assert ends and "mode=lineup_only" in ends[0]

    def test_session_end_counts_the_sells_the_exit_path_made(self, tmp_path, monkeypatch, caplog):
        """The lineup-only exit used to hard-code `sells=0`, so a deferred
        sell plan executed in step 1 was reported as no sale at all."""
        squad = TestTheDeferredSellGate._squad_with_the_sell_target()
        with caplog.at_level(logging.INFO, logger="rehoboam.auto_trader"):
            _run(
                squad,
                "full",
                tmp_path,
                monkeypatch,
                phase="locked",
                days=1,
                deferred_sell_ids=["x"],
            )
        ends = [m for m in caplog.messages if m.startswith("session-end")]
        assert ends and "sells=1" in ends[0], ends

    def test_a_locked_session_logs_session_end_too(self, tmp_path, monkeypatch, caplog):
        """Today the locked branch returns without a session-end line, which
        is why App Insights shows none for 2026-09-10 20:00 and 2026-09-11 08:00."""
        with caplog.at_level(logging.INFO, logger="rehoboam.auto_trader"):
            _run(_legal_squad(), "full", tmp_path, monkeypatch, phase="locked", days=1)
        assert any(m.startswith("session-end") and "mode=full" in m for m in caplog.messages)
