"""The flip book in the replay (M2-17): a toy world with one riser that turns."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from rehoboam.desk import drift
from rehoboam.sim import engine
from rehoboam.sim.engine import Policy, SimState, World
from rehoboam.sim.history import Listing, Prediction, Round

UTC = timezone.utc
D0 = date(2026, 10, 1)
START = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)
END = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
CORE = {"g": "Goalkeeper", **{f"d{i}": "Defender" for i in range(4)}}
CORE |= {f"m{i}": "Midfielder" for i in range(4)} | {"f0": "Forward", "f1": "Forward"}
DAYS = [D0 + timedelta(days=i) for i in range(-25, 16)]


def _riser(day: date) -> int:
    """+5% a night until 10-06, then -4% a night: the turn is the night of 10-07."""
    peak = D0 + timedelta(days=5)
    if day <= peak:
        return round(500_000 * 1.05 ** (day - (D0 - timedelta(days=25))).days)
    return round(_riser(peak) * 0.96 ** (day - peak).days)


def _star(day: date) -> int:
    """Flat at 4m, then falling from 10-06: a turn that must not sell a core player."""
    return 4_000_000 if day <= D0 + timedelta(days=4) else 3_800_000


def _world(with_star: bool = False) -> World:
    paths = {pid: {d: 5_000_000 for d in DAYS} for pid in CORE}
    paths["bench"] = {d: 2_000_000 for d in DAYS}
    paths["riser"] = {d: _riser(d) for d in DAYS}
    if with_star:
        paths["star"] = {d: _star(d) for d in DAYS}
    moves = drift.market_moves(paths)

    def table(lag):
        samples = drift.training_samples(
            paths, moves, lag=lag, horizon=7, first_day=DAYS[0], last_day=D0 - timedelta(days=8)
        )
        return drift.fit(samples, lag=lag, horizon=7)

    seen = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    listing = Listing(
        "riser",
        listed_at=datetime(2026, 10, 2, 8, 30, tzinfo=UTC),
        expires_at=datetime(2026, 10, 3, 13, 0, tzinfo=UTC),
        last_seen=seen,
        market_value=_riser(D0 + timedelta(days=1)),
    )
    star = Listing(
        "star",
        listed_at=datetime(2026, 10, 2, 8, 30, tzinfo=UTC),
        expires_at=datetime(2026, 10, 3, 13, 0, tzinfo=UTC),
        last_seen=seen,
        market_value=4_000_000,
    )
    eps = {pid: 60.0 for pid in CORE} | {"bench": 5.0, "riser": 20.0, "star": 140.0}
    before = START - timedelta(days=1)
    return World(
        paths=paths,
        moves=moves,
        hold_table=table(0),
        buy_table=table(1),
        listings=(listing, star) if with_star else (listing,),
        rivals={},
        predictions={pid: (Prediction(before, ep, 1),) for pid, ep in eps.items()},
        positions=CORE | {"bench": "Midfielder", "riser": "Forward", "star": "Forward"},
        points={},
        rounds=(Round(1, KICKOFF),),
        rival_buys=tuple((START - timedelta(days=10), 3_000_000, 0.05) for _ in range(25)),
        eur_per_point=200_000.0,
        reset_at=datetime(2026, 12, 20, tzinfo=UTC),
    )


def _run():
    state = SimState(cash=-1_000_000, held=frozenset({*CORE, "bench"}))
    policy = Policy(flipbook=True, planned_debt=True)
    return engine.run(_world(), state, START, END, policy)


def test_the_bench_is_sold_and_the_riser_is_bought_on_debt():
    events = _run().events

    assert ("sell", "bench") in {(e.kind, e.player_id) for e in events if e.at == START}
    assert [e.kind for e in events if e.player_id == "riser"][:2] == ["bid", "won"]


def test_the_riser_is_sold_by_the_first_session_that_sees_his_turn():
    # 20:00 UTC is 22:00 Berlin in October: the new values are in force.
    sale = next(e for e in _run().events if e.kind == "sell" and e.player_id == "riser")

    assert sale.at == datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
    assert sale.amount == _riser(date(2026, 10, 7))


def test_the_wallet_is_never_negative_at_the_kickoff():
    final = _run()

    assert final.violations == ()
    assert [m.cash_at_kickoff >= 0 for m in final.matchdays] == [True]


def test_the_core_buys_a_points_upgrade_sells_who_it_displaces_and_keeps_him_through_a_turn():
    state = SimState(cash=-1_000_000, held=frozenset({*CORE, "bench"}))
    policy = Policy(flipbook=True, planned_debt=True, core_upgrades=True)
    final = engine.run(_world(with_star=True), state, START, END, policy)
    events = final.events

    won = next(e for e in events if e.kind == "won" and e.player_id == "star")
    displaced = [e.player_id for e in events if e.kind == "sell" and e.at > won.at][:1]

    assert won.book == "core"
    assert displaced and displaced[0] in CORE  # whichever 60-pointer the best formation drops
    assert not any(e.kind == "sell" and e.player_id == "star" for e in events)
    assert final.violations == ()


def test_form_is_the_season_average_of_appearances_finished_before_the_day():
    from dataclasses import replace

    apps = {
        "f0": tuple((D0 - timedelta(days=d), pts) for d, pts in ((30, 100), (20, 120), (10, 140)))
    }
    world = replace(_world(), appearances={"f0": tuple(sorted(apps["f0"]))})

    assert engine.form_at(world, "f0", D0) == 120.0
    assert engine.form_at(world, "f0", D0 - timedelta(days=10)) is None  # two before: too few
    assert engine.form_at(world, "g", D0) is None


def test_the_blend_reads_real_form_times_the_chance_of_appearing():
    from dataclasses import replace

    from rehoboam.desk.points import PriceBlend

    apps = tuple((D0 - timedelta(days=d), 120) for d in (30, 20, 10))  # 120 a game
    before = START - timedelta(days=1)
    world = replace(
        _world(),
        appearances={"f0": apps},
        predictions={"f0": (Prediction(before, 60.0, 1, p_appear=0.5),)},
        blends=((before, PriceBlend(0.0, 1.0, 0.0, rows=100, form_weight=1.0)),),
    )

    player = engine._player(world, "f0", START, 5_000_000, 0.0)

    assert player.ep_next == 60.0  # real form x P(appear) = 120 x 0.5 = 60: the model was right
