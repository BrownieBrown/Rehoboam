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
from dataclasses import dataclass
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


def start_state(league: League) -> SimState:
    cash = max((r for r in league.wallet if r.at <= W1_START), key=lambda r: r.at).budget
    return SimState(cash=cash, held=holdings_at(league, _us(league), W1_START))


def window_rounds(world: World) -> tuple[Round, ...]:
    return tuple(r for r in world.rounds if W1_START < r.kickoff <= W1_END)


def _squad_value(world: World, held: frozenset[str], at: datetime) -> int:
    return sum(engine.value_at(world, p, value_day(at)) or 0 for p in held)


def manager_line(world: World, league: League, manager_id: str, rounds: Sequence[Round]) -> Line:
    start = holdings_at(league, manager_id, W1_START)
    end = holdings_at(league, manager_id, W1_END)
    cash = sum(
        (t.price if t.kind == SELL else -t.price if t.kind == BUY else 0)
        for t in league.transfers
        if t.manager_id == manager_id and W1_START < t.at <= W1_END
    )
    change = _squad_value(world, end, W1_END) - _squad_value(world, start, W1_START) + cash
    points = [league.matchday_points.get((manager_id, r.day_number), 0.0) for r in rounds]
    return Line(league.names.get(manager_id, manager_id), change, sum(points) / len(points))


def _policy_line(world: World, name: str, start: SimState, final: SimState) -> Line:
    change = engine.net_worth(world, final, W1_END) - engine.net_worth(world, start, W1_START)
    points = [m.points for m in final.matchdays]
    return Line(name, change, sum(points) / len(points) if points else 0.0)


def run(world: World, league: League, policy: Policy) -> BacktestReport:
    rounds = window_rounds(world)
    start = start_state(league)
    final = engine.run(world, start, W1_START, W1_END, policy)
    held_only = SimState(cash=start.cash, held=start.held)
    for rnd in rounds:
        held_only = engine.kickoff(world, held_only, rnd)
    managers = sorted({s.manager_id for s in league.snapshots})
    events: dict[str, int] = {}
    for e in final.events:
        events[e.kind] = events.get(e.kind, 0) + 1
    return BacktestReport(
        policy=_policy_line(world, f"v3 (mu x{policy.mu_scale:g})", start, final),
        do_nothing=_policy_line(world, "hold the go-live squad", start, held_only),
        actual=manager_line(world, league, _us(league), rounds),
        rivals=tuple(
            manager_line(world, league, m, rounds) for m in managers if m not in league.self_ids
        ),
        matchdays=tuple(r.day_number for r in rounds),
        events=events,
        violations=final.violations,
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
