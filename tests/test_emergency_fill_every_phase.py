"""A short squad is an emergency in EVERY phase, not only `locked` (REH-112).

`_run_emergency_squad_fill` is the one buy path deliberately left autonomous,
because an empty lineup slot costs -100 points. It was nested inside
`if ctx.matchday_phase.phase == "locked":`, so it could only ever run when the
phase detector had found an imminent fixture.

On 2026-08-31 it never ran. `/myeleven` reported no upcoming fixture between
MD2 and MD3, so `days_until_match` was None, `_get_matchday_phase` took its
`else` branch to "moderate", and a squad of 7 sat four slots short of a legal
eleven — a standing -400 — while the emergency fill was unreachable. The same
None also disables the budget-at-kickoff guard, so one failed lookup silently
stood down three safety systems at once.

The `else` branch is conservative about *spending*, which is right, and the
-100 is not spending. The fail-safe has to fail toward fielding an eleven.

These tests prove the CALL SITE, following the precedent set by REH-103's
`TestTheSessionActuallyCallsIt`: a writer that exists and is never called is
how `auction_outcomes.winning_bid` sat NULL for a season (REH-86).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
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


def _squad_of_seven() -> list[Player]:
    """The real 2026-08-31 squad shape: GK 1, DEF 4, MID 2, FW 0."""
    return (
        [_player("gk", "Goalkeeper")]
        + [_player(f"d{i}", "Defender") for i in range(4)]
        + [_player(f"m{i}", "Midfielder") for i in range(2)]
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
        return {"budget": 55_485_928, "team_value": 111_162_700}


def _context(phase: str, days: int | None, squad) -> EPSessionContext:
    return EPSessionContext(
        ep_result={"squad_scores": {}, "market_players": {}},
        matchday_phase=MatchdayPhase(
            days_until_match=days,
            phase=phase,
            max_trades=2,
            allow_flips=False,
            reason=f"test phase {phase}",
        ),
        my_bids=[],
        my_bid_amounts={},
        squad=list(squad),
        current_budget=55_485_928,
        team_value=111_162_700,
        flip_budget=0,
    )


def _run(phase: str, days: int | None, squad, tmp_path, monkeypatch):
    """Drive one session, returning the emergency-fill mock."""
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)

    trader = AutoTrader(api=_Api(squad), settings=Settings(), dry_run=True)
    ctx = _context(phase, days, squad)

    with (
        patch.object(AutoTrader, "_build_session_context", return_value=ctx),
        patch.object(AutoTrader, "_run_emergency_squad_fill", return_value=[]) as fill,
        patch.object(AutoTrader, "run_profit_sell_phase", return_value=[]),
        patch.object(AutoTrader, "optimize_and_execute_squad", return_value=[]),
        patch.object(AutoTrader, "run_unified_trade_phase", return_value=[]),
        patch.object(AutoTrader, "_set_optimal_lineup", return_value=[]),
    ):
        trader.run_full_session(LEAGUE)

    return fill


class TestTheEmergencyFillIsNotGatedOnPhase:
    @pytest.mark.parametrize(
        ("phase", "days"),
        [
            ("moderate", None),  # the 2026-08-31 state: no fixture visible
            ("moderate", 3),
            ("aggressive", 6),
            ("matchday_in_progress", 0),
            ("locked", 1),  # must keep working, not regress
        ],
    )
    def test_a_squad_short_of_an_eleven_triggers_the_fill(self, phase, days, tmp_path, monkeypatch):
        fill = _run(phase, days, _squad_of_seven(), tmp_path, monkeypatch)

        assert fill.called, f"emergency fill never ran in phase {phase!r}"
        assert fill.call_args.args[3] == 4, "should buy the 4 players an eleven needs"

    @pytest.mark.parametrize("phase", ["moderate", "aggressive", "locked"])
    def test_a_fieldable_squad_never_triggers_the_fill(self, phase, tmp_path, monkeypatch):
        squad = (
            [_player("gk", "Goalkeeper")]
            + [_player(f"d{i}", "Defender") for i in range(4)]
            + [_player(f"m{i}", "Midfielder") for i in range(4)]
            + [_player(f"f{i}", "Forward") for i in range(2)]
        )

        fill = _run(phase, 3, squad, tmp_path, monkeypatch)

        assert not fill.called

    def test_the_fill_runs_once_not_twice(self, tmp_path, monkeypatch):
        """Hoisting it out of the locked branch must not leave two call sites."""
        fill = _run("locked", 1, _squad_of_seven(), tmp_path, monkeypatch)

        assert fill.call_count == 1
