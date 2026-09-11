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
            (3, 6, 1),
            (4, 2, 4),
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


class _ProposalSpy:
    """The emergency fill proposes rather than buys since REH-114."""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def __call__(self, league, rec, ctx, *, bid=None, auto_approve_at=None):
        self.calls.append((rec.player.id, int(bid if bid is not None else rec.recommended_bid)))
        return True

    @property
    def ids(self) -> list[str]:
        return [c[0] for c in self.calls]


def _gated_rec(pid: str, position: str, ep: float, price: int, bid: int | None = None):
    """A buy rec the real `_build_buy_gate` can read: it needs `market_value`
    and `team_id`, which the display-only namespace above does not carry."""
    return SimpleNamespace(
        player=SimpleNamespace(
            id=pid,
            first_name="F",
            last_name=f"P{pid}",
            position=position,
            price=price,
            market_value=price,
            team_id=f"club-{pid}",
            average_points=10.0,
            status=0,
        ),
        recommended_bid=price if bid is None else bid,
        marginal_ep_gain=ep,
        sell_plan=None,
    )


def _gated_ctx(squad, recs, budget: int) -> EPSessionContext:
    return EPSessionContext(
        ep_result={
            "buy_recs": list(recs),
            "squad_scores": [],
            # The gate treats `market_players` as a security boundary: a
            # candidate it has never seen cannot be bought at all.
            "market_players": {r.player.id: r.player for r in recs},
        },
        matchday_phase=MatchdayPhase(
            days_until_match=4, phase="moderate", max_trades=2, allow_flips=False, reason="t"
        ),
        my_bids=[],
        my_bid_amounts={},
        squad=list(squad),
        current_budget=budget,
        team_value=144_177_545,
        flip_budget=0,
    )


class TestTheReservesWalkHonoursTheGap:
    """A reserve is only a reserve while it still closes something.

    The basket refuses a buy that closes no slot; the walk behind it used to
    not, so a second midfielder could be proposed for a shortfall only a
    forward can close — the same EUR-for-nothing the seventh defender was.
    """

    def test_a_second_pick_that_closes_nothing_is_skipped(self, monkeypatch, tmp_path):
        """GK 1, DEF 6, MID 3, FW 0: two short. The first midfielder closes
        one slot (5-4-1 minus a forward); the second closes none, because
        only a forward can close what is left."""
        squad = _squad(1, 6, 3, 0)
        recs = [
            _gated_rec("m1", "Midfielder", 70.0, 1_000_000),
            _gated_rec("m2", "Midfielder", 60.0, 1_000_000),
        ]
        api = _Api(squad)
        trader = _trader(api, monkeypatch, tmp_path)
        trader._propose_buy = spy = _ProposalSpy()

        with patch.object(AutoTrader, "_is_wash_trade", return_value=False):
            results = trader._run_emergency_squad_fill(
                LEAGUE, _gated_ctx(squad, recs, 12_929_567), squad, slots_short=2
            )

        assert spy.ids == ["m1"], "the second midfielder closes nothing"
        assert sum(1 for r in results if r.success) == 1

    def test_a_saturated_reserve_is_never_reached_when_the_closer_is_refused(
        self, monkeypatch, tmp_path
    ):
        """The 2026-09-11 squad, one short at Midfielder or Forward. The only
        closer is priced 40% over market value, which the gate refuses; the
        affordable defender closes nothing, so the answer is to buy nobody."""
        squad = _squad(1, 6, 3, 1)
        recs = [
            _gated_rec("m1", "Midfielder", 30.0, 5_000_000, bid=7_000_000),
            _gated_rec("d1", "Defender", 90.0, 1_000_000),
        ]
        api = _Api(squad)
        trader = _trader(api, monkeypatch, tmp_path)
        trader._propose_buy = spy = _ProposalSpy()

        with patch.object(AutoTrader, "_is_wash_trade", return_value=False):
            results = trader._run_emergency_squad_fill(
                LEAGUE, _gated_ctx(squad, recs, 12_929_567), squad, slots_short=1
            )

        assert spy.ids == []
        assert results == []
