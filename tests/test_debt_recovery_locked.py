"""The locked window repays the debt the earlier phases were allowed to run.

Marco's rule of 2026-09-22: the bot may sit in the red until gameday, always.
A negative budget AT kickoff is zero points for the whole matchday, so the
last sessions before a round (`phase == "locked"`, the final two days) must
sell the wallet back to zero — net of open offers, because one that is won
after the session and before kickoff drains the wallet too.

These tests prove the CALL SITE, the way REH-112's tests do for the
emergency fill: the sell reaches `api.sell_player_instant`, it happens before
the fill (so the fill can spend what the sale freed), and it happens in the
locked window only.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.config import Settings
from rehoboam.kickbase_client import Player

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


def _player(pid: str, position: str, mv: int = 5_000_000, buy: int = 5_000_000) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=mv,
        points=0,
        average_points=50.0,
        buy_price=buy,
    )


def _squad() -> list[Player]:
    """Eleven starters plus one bench midfielder held at a profit."""
    return (
        [_player("gk", "Goalkeeper")]
        + [_player(f"d{i}", "Defender") for i in range(4)]
        + [_player(f"m{i}", "Midfielder") for i in range(4)]
        + [_player(f"f{i}", "Forward") for i in range(2)]
        + [_player("bench", "Midfielder", mv=6_000_000, buy=4_000_000)]
    )


def _scores(squad: list[Player]) -> list:
    return [
        SimpleNamespace(player_id=p.id, expected_points=10.0 if p.id == "bench" else 60.0)
        for p in squad
    ]


def _api(squad: list[Player]) -> MagicMock:
    """A Kickbase that forgets a player the moment he is sold."""
    roster = list(squad)
    api = MagicMock()
    api.user = SimpleNamespace(id="3616202")
    api.get_squad.side_effect = lambda league: list(roster)
    api.get_my_bids.return_value = []
    api.get_team_info.return_value = {"budget": 0, "team_value": 60_000_000}

    def sell(league, player):
        roster[:] = [p for p in roster if p.id != player.id]
        return {"ok": True}

    api.sell_player_instant.side_effect = sell
    return api


def _context(phase: str, days: int | None, squad, budget: int, offers: dict | None = None):
    return EPSessionContext(
        ep_result={"squad_scores": _scores(squad), "market_players": {}},
        matchday_phase=MatchdayPhase(
            days_until_match=days,
            phase=phase,
            max_trades=2,
            allow_flips=False,
            reason=f"test phase {phase}",
        ),
        my_bids=[],
        my_bid_amounts=dict(offers or {}),
        squad=list(squad),
        current_budget=budget,
        team_value=60_000_000,
        flip_budget=0,
    )


def _run(phase, days, budget, tmp_path, monkeypatch, *, offers=None, mode="full"):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.chdir(tmp_path)

    squad = _squad()
    api = _api(squad)
    trader = AutoTrader(api=api, settings=Settings(), dry_run=False)
    ctx = _context(phase, days, squad, budget, offers)

    sold_before_fill: list[int] = []

    def fill(*args, **kwargs):
        sold_before_fill.append(api.sell_player_instant.call_count)
        return []

    with (
        patch.object(AutoTrader, "_build_session_context", return_value=ctx),
        patch.object(AutoTrader, "_run_emergency_squad_fill", side_effect=fill),
        patch.object(AutoTrader, "run_profit_sell_phase", return_value=[]),
        patch.object(AutoTrader, "optimize_and_execute_squad", return_value=[]),
        patch.object(AutoTrader, "run_unified_trade_phase", return_value=[]),
        patch.object(AutoTrader, "_set_optimal_lineup", return_value=[]),
        patch(
            "rehoboam.services.trend_service.TrendService.get_trend",
            return_value=SimpleNamespace(trend_7d_pct=None),
        ),
    ):
        session = trader.run_full_session(LEAGUE)

    return api, ctx, session, sold_before_fill


class TestTheLockedWindowRepaysTheDebt:
    def test_a_negative_wallet_sells_the_cheapest_sacrifice(self, tmp_path, monkeypatch):
        api, ctx, session, _ = _run("locked", 1, -5_000_000, tmp_path, monkeypatch)

        sold = [
            (c.kwargs.get("player") or c.args[-1]).id
            for c in api.sell_player_instant.call_args_list
        ]
        assert sold == ["bench"], "the profitable bench player goes, not a starter"
        assert ctx.current_budget == 1_000_000
        assert [r.action for r in session.lineup_trades if r.success] == ["SELL"]
        assert session.total_earned == 6_000_000

    def test_open_offers_count_as_spent(self, tmp_path, monkeypatch):
        """Wallet +3m, offers 8m: a win before kickoff leaves -5m (rule I3)."""
        api, _, _, _ = _run("locked", 1, 3_000_000, tmp_path, monkeypatch, offers={"x": 8_000_000})
        assert api.sell_player_instant.call_count == 1

    def test_it_runs_before_the_emergency_fill(self, tmp_path, monkeypatch):
        """The fill spends what the sale frees, so the sale has to come first.
        -8m needs the bench player (6m) AND a starter (5m); the squad drops
        to ten and the fill is reached with both sales already on the API."""
        api, _, _, sold_before_fill = _run("locked", 0, -8_000_000, tmp_path, monkeypatch)
        assert api.sell_player_instant.call_count == 2
        assert sold_before_fill == [2]

    def test_it_runs_in_lineup_only_mode_too(self, tmp_path, monkeypatch):
        """Zero points at kickoff is a rule, not a trade — like the Top-5
        forced sale, it is not something the mode switches off."""
        api, _, _, _ = _run("locked", 1, -5_000_000, tmp_path, monkeypatch, mode="lineup_only")
        assert api.sell_player_instant.call_count == 1


class TestItStaysOutOfTheWayOtherwise:
    def test_a_positive_wallet_sells_nobody(self, tmp_path, monkeypatch):
        api, _, _, _ = _run("locked", 1, 2_000_000, tmp_path, monkeypatch)
        assert api.sell_player_instant.call_count == 0

    @pytest.mark.parametrize(("phase", "days"), [("aggressive", 6), ("moderate", 3)])
    def test_debt_is_allowed_to_stand_before_the_locked_window(
        self, phase, days, tmp_path, monkeypatch
    ):
        api, _, _, _ = _run(phase, days, -5_000_000, tmp_path, monkeypatch)
        assert api.sell_player_instant.call_count == 0
