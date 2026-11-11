"""M2: the planner over window W1, against the league (spec v3 §8.4).

Net worth is transfer-only for everyone: team value, plus sales, minus buys.
Rivals' cash is never visible, but their transfers are. The bonuses (daily,
points) are left out on both sides, so the comparison is like for like.

The bar, written before the first run:

- B1: net-worth change ≥ the rivals' median;
- B2: points per matchday ≥ the rivals' median;
- B3: no invariant violated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from statistics import median

from rehoboam.desk import drift
from rehoboam.sim import auction, engine, history, validate
from rehoboam.sim.engine import Policy, SimState, World
from rehoboam.sim.history import BUY, SELL, Round, SquadSnapshot, Transfer, value_day
from rehoboam.store.transfer_study import eur_per_point

W1_START = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)  # go-live
W1_END = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
RESET_AT = datetime(2026, 12, 20, tzinfo=timezone.utc)  # after MD14, the last round of 2026
SEASON = "2026/2027"
HORIZON_DAYS = 7
FALLBACK_EUR_PER_POINT = 255_008.0  # measured 2026-09-23, used only if the fit is too loose


@dataclass(frozen=True)
class Line:
    name: str
    net_worth_change: int
    points_per_matchday: float


@dataclass(frozen=True)
class BacktestReport:
    policy: Line
    do_nothing: Line
    actual: Line
    rivals: tuple[Line, ...]
    matchdays: tuple[int, ...]
    events: Mapping[str, int]
    violations: tuple[str, ...]

    @property
    def rival_median(self) -> Line:
        return Line(
            "rivals' median",
            round(median(r.net_worth_change for r in self.rivals)),
            median(r.points_per_matchday for r in self.rivals),
        )

    @property
    def b1(self) -> bool:
        return self.policy.net_worth_change >= self.rival_median.net_worth_change

    @property
    def b2(self) -> bool:
        return self.policy.points_per_matchday >= self.rival_median.points_per_matchday

    @property
    def b3(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class League:
    snapshots: tuple[SquadSnapshot, ...]
    transfers: tuple[Transfer, ...]
    matchday_points: Mapping[tuple[str, int], float]
    names: Mapping[str, str]
    self_ids: frozenset[str]
    wallet: tuple[history.WalletReading, ...]


def load(conn, *, fit_cutoff: date) -> tuple[World, League]:
    paths = history.load_value_paths(conn)
    moves = drift.market_moves(paths)
    transfers = history.load_transfers(conn, since=datetime(2026, 7, 1, tzinfo=timezone.utc))
    self_ids = history.load_self_ids(conn)
    listings = history.load_kickbase_listings(conn, expired_before=W1_END)
    buys = auction.buys_by_player(transfers)
    predictions, positions = history.load_predictions(conn, since=W1_START - timedelta(days=7))
    world = World(
        paths=paths,
        moves=moves,
        hold_table=_fit(paths, moves, fit_cutoff, lag=0),
        buy_table=_fit(paths, moves, fit_cutoff, lag=1),
        listings=listings,
        rivals={
            (lst.player_id, lst.listed_at): auction.rival_for(lst, buys, paths, self_ids)
            for lst in listings
        },
        predictions=predictions,
        positions=positions,
        points=history.load_points(conn, season=SEASON),
        rounds=history.load_rounds(conn, season=SEASON),
        rival_buys=engine.rival_buys(transfers, paths, self_ids),
        eur_per_point=eur_per_point(conn) or FALLBACK_EUR_PER_POINT,
        reset_at=RESET_AT,
    )
    league = League(
        snapshots=history.load_squad_snapshots(conn),
        transfers=transfers,
        matchday_points=history.load_matchday_points(conn),
        names=history.load_manager_names(conn),
        self_ids=self_ids,
        wallet=history.load_wallet(conn),
    )
    return world, league


def _fit(paths, moves, cutoff: date, *, lag: int) -> drift.DriftTable:
    first = min(day for path in paths.values() for day in path)
    samples = drift.training_samples(
        paths,
        moves,
        lag=lag,
        horizon=HORIZON_DAYS,
        first_day=first,
        last_day=cutoff - timedelta(days=lag + HORIZON_DAYS),
    )
    return drift.fit(samples, lag=lag, horizon=HORIZON_DAYS)


def holdings_at(league: League, manager_id: str, at: datetime) -> frozenset[str]:
    snaps = sorted((s for s in league.snapshots if s.manager_id == manager_id), key=lambda s: s.at)
    first = snaps[0]
    mine = [t for t in league.transfers if t.manager_id == manager_id]
    return validate.replay_holdings(frozenset(first.players), mine, first.at, at)


def _us(league: League) -> str:
    return sorted(league.self_ids)[0]


def start_state(league: League, start: datetime = W1_START) -> SimState:
    cash = max((r for r in league.wallet if r.at <= start), key=lambda r: r.at).budget
    return SimState(cash=cash, held=holdings_at(league, _us(league), start))


def window_rounds(world: World, start: datetime = W1_START) -> tuple[Round, ...]:
    return tuple(r for r in world.rounds if start < r.kickoff <= W1_END)


def _squad_value(world: World, held: frozenset[str], at: datetime) -> int:
    return sum(engine.value_at(world, p, value_day(at)) or 0 for p in held)


def manager_line(
    world: World,
    league: League,
    manager_id: str,
    rounds: Sequence[Round],
    start: datetime = W1_START,
) -> Line:
    held_start = holdings_at(league, manager_id, start)
    held_end = holdings_at(league, manager_id, W1_END)
    cash = sum(
        (t.price if t.kind == SELL else -t.price if t.kind == BUY else 0)
        for t in league.transfers
        if t.manager_id == manager_id and start < t.at <= W1_END
    )
    change = _squad_value(world, held_end, W1_END) - _squad_value(world, held_start, start) + cash
    points = [league.matchday_points.get((manager_id, r.day_number), 0.0) for r in rounds]
    return Line(league.names.get(manager_id, manager_id), change, sum(points) / len(points))


def _policy_line(
    world: World, name: str, first: SimState, final: SimState, start: datetime = W1_START
) -> Line:
    change = engine.net_worth(world, final, W1_END) - engine.net_worth(world, first, start)
    points = [m.points for m in final.matchdays]
    return Line(name, change, sum(points) / len(points) if points else 0.0)


def _with_blends(world: World, policy: Policy) -> World:
    if policy.price_blend and not world.blends:
        return replace(world, blends=engine.fit_blends(world))
    return world


def run(world: World, league: League, policy: Policy, start: datetime = W1_START) -> BacktestReport:
    world = _with_blends(world, policy)
    rounds = window_rounds(world, start)
    first = start_state(league, start)
    final = engine.run(world, first, start, W1_END, policy)
    held_only = SimState(cash=first.cash, held=first.held)
    for rnd in rounds:
        held_only = engine.kickoff(world, held_only, rnd)
    managers = sorted({s.manager_id for s in league.snapshots})
    events: dict[str, int] = {}
    for e in final.events:
        events[e.kind] = events.get(e.kind, 0) + 1
    return BacktestReport(
        policy=_policy_line(
            world,
            f"v3 (mu x{policy.mu_scale:g}{', blend' if policy.price_blend else ''})",
            first,
            final,
            start,
        ),
        do_nothing=_policy_line(world, "hold the starting squad", first, held_only, start),
        actual=manager_line(world, league, _us(league), rounds, start),
        rivals=tuple(
            manager_line(world, league, m, rounds, start)
            for m in managers
            if m not in league.self_ids
        ),
        matchdays=tuple(r.day_number for r in rounds),
        events=events,
        violations=final.violations,
    )


# ---------------------------------------------------------------------------
# The ensemble: one configuration judged on many paths, not one draw
# ---------------------------------------------------------------------------

ENSEMBLE_STARTS = tuple(W1_START + timedelta(days=2 * i) for i in range(8))  # 09-22 → 10-06


@dataclass(frozen=True)
class EnsembleReport:
    name: str
    money_excess: tuple[int, ...]  # per path: policy minus the rivals' median, same window
    points_excess: tuple[float, ...]
    violations: int
    # Against our real record from the same start: independent of the starting squad.
    money_vs_real: tuple[int, ...] = ()
    points_vs_real: tuple[float, ...] = ()

    @property
    def mean_money(self) -> float:
        return sum(self.money_excess) / len(self.money_excess)

    @property
    def mean_points(self) -> float:
        return sum(self.points_excess) / len(self.points_excess)

    @property
    def b1(self) -> bool:
        return self.mean_money >= 0

    @property
    def b2(self) -> bool:
        return self.mean_points >= 0


def ensemble(
    world: World, league: League, policy: Policy, starts: Sequence[datetime] = ENSEMBLE_STARTS
) -> EnsembleReport:
    world = _with_blends(world, policy)
    reports = [run(world, league, policy, start) for start in starts]
    return EnsembleReport(
        name=reports[0].policy.name,
        money_excess=tuple(
            r.policy.net_worth_change - r.rival_median.net_worth_change for r in reports
        ),
        points_excess=tuple(
            r.policy.points_per_matchday - r.rival_median.points_per_matchday for r in reports
        ),
        violations=sum(len(r.violations) for r in reports),
        money_vs_real=tuple(r.policy.net_worth_change - r.actual.net_worth_change for r in reports),
        points_vs_real=tuple(
            r.policy.points_per_matchday - r.actual.points_per_matchday for r in reports
        ),
    )


def choose(reports: Sequence[EnsembleReport]) -> EnsembleReport:
    """The rule written before the first ensemble run (spec §12): points first.

    Among configurations whose mean points excess is ≥ 0, the highest mean money
    excess. If none reaches the points bar, the highest mean points excess.
    """
    clean = [r for r in reports if r.violations == 0]
    on_points = [r for r in clean if r.b2]
    if on_points:
        return max(on_points, key=lambda r: r.mean_money)
    return max(clean, key=lambda r: r.mean_points)


def format_ensemble(r: EnsembleReport) -> str:
    return (
        f"  {r.name[:24]:<24} money vs median {r.mean_money:>+13,.0f} "
        f"[{min(r.money_excess):+,} .. {max(r.money_excess):+,}]  "
        f"pts/MD vs median {r.mean_points:>+7.1f} "
        f"[{min(r.points_excess):+.0f} .. {max(r.points_excess):+.0f}]  "
        f"B1 {'PASS' if r.b1 else 'FAIL'} B2 {'PASS' if r.b2 else 'FAIL'} "
        f"violations {r.violations}"
    )


def format_report(r: BacktestReport) -> list[str]:
    def row(line: Line) -> str:
        return f"  {line.name[:26]:<26} {line.net_worth_change:>+14,}  {line.points_per_matchday:>8.1f}"

    return [
        f"W1 {W1_START:%Y-%m-%d %H:%M} -> {W1_END:%Y-%m-%d %H:%M} UTC, matchdays {list(r.matchdays)}",
        f"  {'':<26} {'net worth chg':>14}  {'pts/MD':>8}",
        row(r.policy),
        row(r.do_nothing),
        row(r.actual) + "  (us, real)",
        row(r.rival_median),
        "  rivals:",
        *(row(line) for line in sorted(r.rivals, key=lambda x: -x.points_per_matchday)),
        "",
        f"  policy events: {dict(sorted(r.events.items()))}",
        f"  B1 money >= rivals' median: {'PASS' if r.b1 else 'FAIL'}",
        f"  B2 points >= rivals' median: {'PASS' if r.b2 else 'FAIL'}",
        f"  B3 no violations: {'PASS' if r.b3 else 'FAIL ' + '; '.join(r.violations)}",
    ]
