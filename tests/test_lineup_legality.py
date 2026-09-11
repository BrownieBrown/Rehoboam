"""The session never submits an illegal lineup and fills the right position.

Two call sites, tested at the call site (the REH-103 precedent): a writer that
exists and is never called is how a season of NULLs happens.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from rehoboam.auto_trader import (
    AutoTrader,
    EPSessionContext,
    MatchdayPhase,
    _emergency_slots_short,
)
from rehoboam.config import Settings
from rehoboam.kickbase_client import Player

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


def _player(pid: str, position: str, price: int = 1_000_000) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=price,
        points=0,
        average_points=50.0,
    )


def _squad(gk: int, de: int, mi: int, fw: int) -> list[Player]:
    return (
        [_player(f"g{i}", "Goalkeeper") for i in range(gk)]
        + [_player(f"d{i}", "Defender") for i in range(de)]
        + [_player(f"m{i}", "Midfielder") for i in range(mi)]
        + [_player(f"f{i}", "Forward") for i in range(fw)]
    )


class _Api:
    user = SimpleNamespace(id="3616202")

    def __init__(self, squad):
        self._squad = squad
        self.lineups: list[tuple[str, list[str]]] = []

    def get_squad(self, league):
        return list(self._squad)

    def get_my_bids(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": 12_929_567, "team_value": 144_177_545}

    def set_lineup(self, league, formation, player_ids):
        self.lineups.append((formation, list(player_ids)))
        return {}


def _scores(squad):
    return [SimpleNamespace(player_id=p.id, expected_points=50.0) for p in squad]


def _trader(api, monkeypatch, tmp_path, dry_run=False) -> AutoTrader:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    return AutoTrader(api=api, settings=Settings(), dry_run=dry_run)


class TestSlotsShortUsesLegalFormations:
    def test_six_defenders_is_one_short(self):
        assert _emergency_slots_short(_squad(1, 6, 3, 1)) == 1

    def test_a_legal_squad_is_not_short(self):
        assert _emergency_slots_short(_squad(1, 4, 4, 2)) == 0


class TestTheLineupStepRefusesTenNames:
    def test_illegal_eleven_is_not_submitted_and_is_an_error(self, monkeypatch, tmp_path):
        api = _Api(_squad(1, 6, 3, 1))
        trader = _trader(api, monkeypatch, tmp_path)
        errors: list[str] = []

        lineup = trader._set_optimal_lineup(LEAGUE, errors, squad_scores=_scores(api._squad))

        assert lineup == []
        assert api.lineups == [], "ten names must never reach Kickbase"
        assert any("lineup not legal" in e for e in errors)

    def test_legal_eleven_is_submitted_with_a_legal_formation(self, monkeypatch, tmp_path):
        api = _Api(_squad(1, 5, 5, 3))
        trader = _trader(api, monkeypatch, tmp_path)
        errors: list[str] = []

        lineup = trader._set_optimal_lineup(LEAGUE, errors, squad_scores=_scores(api._squad))

        assert len(lineup) == 11
        assert errors == []
        ((formation, ids),) = api.lineups
        assert len(ids) == 11
        d, m, f = (int(x) for x in formation.split("-"))
        assert (d, m, f) in {
            (3, 4, 3),
            (3, 5, 2),
            (4, 3, 3),
            (4, 4, 2),
            (4, 5, 1),
            (5, 2, 3),
            (5, 3, 2),
            (5, 4, 1),
        }


class TestTheFillTargetsTheOpenPosition:
    def _ctx(self, squad, buy_recs) -> EPSessionContext:
        return EPSessionContext(
            ep_result={"buy_recs": buy_recs, "squad_scores": [], "market_players": {}},
            matchday_phase=MatchdayPhase(
                days_until_match=4,
                phase="moderate",
                max_trades=2,
                allow_flips=False,
                reason="t",
            ),
            my_bids=[],
            my_bid_amounts={},
            squad=list(squad),
            current_budget=12_929_567,
            team_value=144_177_545,
            flip_budget=0,
        )

    def _rec(self, pid: str, position: str, ep: float, price: int):
        # `_run_emergency_squad_fill` reads `rec.player.price`, which only a
        # MarketPlayer carries; a namespace with the fields it touches is enough.
        return SimpleNamespace(
            player=SimpleNamespace(
                id=pid,
                first_name="F",
                last_name=f"P{pid}",
                position=position,
                price=price,
            ),
            recommended_bid=price,
            marginal_ep_gain=ep,
        )

    def test_gap_after_and_fills_gap_describe_the_real_squad(self, monkeypatch, tmp_path):
        squad = _squad(1, 6, 3, 1)
        recs = [
            self._rec("3759", "Defender", 63.5, 22_308_405),
            self._rec("17288", "Midfielder", 68.4, 6_239_298),
            self._rec("17203", "Forward", 62.4, 4_398_390),
        ]
        api = _Api(squad)
        trader = _trader(api, monkeypatch, tmp_path, dry_run=True)
        ctx = self._ctx(squad, recs)

        with (
            patch(
                "rehoboam.services.emergency_basket.select_emergency_basket",
                return_value=[],
            ) as basket,
            patch.object(AutoTrader, "_is_wash_trade", return_value=False),
        ):
            trader._run_emergency_squad_fill(LEAGUE, ctx, squad, slots_short=1)

        assert basket.called
        candidates = basket.call_args.args[0]
        gap_after = basket.call_args.kwargs["gap_after"]
        assert {c.id: c.fills_gap for c in candidates} == {
            "3759": False,
            "17288": True,
            "17203": True,
        }
        assert gap_after(["Midfielder"]) == 0
        assert gap_after(["Forward"]) == 0
        assert gap_after(["Defender"]) == 1
