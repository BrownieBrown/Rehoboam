"""The planner replayed against the league (spec v3 §8.4, ADR-0005, ADR-0007).

Net worth is transfer-only for everyone: team value, plus sales, minus buys.
Rivals' cash is never visible, but their transfers are. The bonuses (daily,
points) are left out on both sides, so the comparison is like for like.

The bar (ADR-0007):

- **Before the reset** (unequal starts): beat our own real record from the same
  start, on money and on points.
- **From the reset** (everyone starts from zero): beat the rivals' median on both.
- **Always:** no invariant violated.

A configuration is judged on an ensemble of paths, never on one draw. A frozen
configuration is judged only on rounds that kicked off after its freeze
(`holdout`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from statistics import median

from rehoboam.desk import drift
from rehoboam.sim import auction, engine, history, validate
from rehoboam.sim.engine import ROUND_SPAN, SESSION_TIMES_UTC, Policy, SimState, World
from rehoboam.sim.history import BUY, SELL, Round, SquadSnapshot, Transfer, value_day
from rehoboam.store.transfer_study import eur_per_point

W1_START = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)  # go-live
W1_END = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
RESET_AT = datetime(2026, 12, 20, tzinfo=timezone.utc)  # after MD14, the last round of 2026
SEASON = "2026/2027"
HORIZON_DAYS = 7
FALLBACK_EUR_PER_POINT = 255_008.0  # measured 2026-09-23, used only if the fit is too loose
PATH_SPACING = timedelta(days=1)  # ADR-0009: daily starts (two days before it)


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


W1 = Window(W1_START, W1_END)


@dataclass(frozen=True)
class Line:
    name: str
    net_worth_change: int
    points_per_matchday: float


@dataclass(frozen=True)
class BacktestReport:
    window: Window
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


def load(
    conn,
    *,
    fit_cutoff: date,
    until: datetime = W1_END,
    hold_refine: Sequence[str] = (),
    buy_refine: Sequence[str] = (),
) -> tuple[World, League]:
    """The world and the league. ``hold_refine`` / ``buy_refine`` name the drift
    refinements each table is fitted with (`drift.REFINEMENTS`, M2-11)."""
    paths = history.load_value_paths(conn)
    moves = drift.market_moves(paths)
    matches = history.load_match_points(conn)
    transfers = history.load_transfers(conn, since=datetime(2026, 7, 1, tzinfo=timezone.utc))
    self_ids = history.load_self_ids(conn)
    listings = history.load_kickbase_listings(conn, expired_before=until)
    buys = auction.buys_by_player(transfers)
    predictions, positions = history.load_predictions(conn, since=W1_START - timedelta(days=7))
    world = World(
        paths=paths,
        moves=moves,
        hold_table=_fit(paths, moves, matches, fit_cutoff, lag=0, refine=hold_refine),
        buy_table=_fit(paths, moves, matches, fit_cutoff, lag=1, refine=buy_refine),
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
        matches=matches,
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


def _fit(
    paths, moves, matches, cutoff: date, *, lag: int, refine: Sequence[str] = ()
) -> drift.DriftTable:
    first = min(day for path in paths.values() for day in path)
    samples = drift.training_samples(
        paths,
        moves,
        lag=lag,
        horizon=HORIZON_DAYS,
        first_day=first,
        last_day=cutoff - timedelta(days=lag + HORIZON_DAYS),
        matches=matches,
    )
    return drift.fit(samples, lag=lag, horizon=HORIZON_DAYS, refine=refine)


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


def window_rounds(world: World, window: Window = W1) -> tuple[Round, ...]:
    """Rounds that kicked off inside the window and were finished by its end."""
    return tuple(
        r for r in world.rounds if window.start < r.kickoff and r.kickoff + ROUND_SPAN <= window.end
    )


def _squad_value(world: World, held: frozenset[str], at: datetime) -> int:
    return sum(engine.value_at(world, p, value_day(at)) or 0 for p in held)


def manager_line(
    world: World, league: League, manager_id: str, rounds: Sequence[Round], window: Window = W1
) -> Line:
    held_start = holdings_at(league, manager_id, window.start)
    held_end = holdings_at(league, manager_id, window.end)
    cash = sum(
        (t.price if t.kind == SELL else -t.price if t.kind == BUY else 0)
        for t in league.transfers
        if t.manager_id == manager_id and window.start < t.at <= window.end
    )
    change = (
        _squad_value(world, held_end, window.end)
        - _squad_value(world, held_start, window.start)
        + cash
    )
    points = [league.matchday_points.get((manager_id, r.day_number), 0.0) for r in rounds]
    return Line(league.names.get(manager_id, manager_id), change, sum(points) / len(points))


def _policy_line(
    world: World, name: str, first: SimState, final: SimState, window: Window = W1
) -> Line:
    change = engine.net_worth(world, final, window.end) - engine.net_worth(
        world, first, window.start
    )
    points = [m.points for m in final.matchdays]
    return Line(name, change, sum(points) / len(points) if points else 0.0)


def _with_blends(world: World, policy: Policy) -> World:
    if policy.price_blend and not world.blends:
        return replace(world, blends=engine.fit_blends(world))
    return world


def _name(policy: Policy, world: World) -> str:
    parts = ["flip book" if policy.flipbook else f"mu x{policy.mu_scale:g}"]
    if policy.price_blend and not policy.flipbook:
        parts.append("blend")
    if policy.planned_debt:
        parts.append("debt")
    if policy.value_bid:
        parts.append("value bid")
    if policy.core_upgrades:
        parts.append("core")
    for label, table in (("hold", world.hold_table), ("buy", world.buy_table)):
        if table.refine:
            parts.append(f"{label} " + "+".join(_SHORT[name] for name in table.refine))
    return f"v3 ({', '.join(parts)})"


_SHORT = {drift.MOVE_14N: "m14", drift.LAST_POINTS: "pts"}


def run(world: World, league: League, policy: Policy, window: Window = W1) -> BacktestReport:
    world = _with_blends(world, policy)
    rounds = window_rounds(world, window)
    first = start_state(league, window.start)
    final = engine.run(world, first, window.start, window.end, policy)
    final = replace(
        final, matchdays=tuple(m for m in final.matchdays if m.day_number in _days(rounds))
    )
    held_only = SimState(cash=first.cash, held=first.held)
    for rnd in rounds:
        held_only = engine.kickoff(world, held_only, rnd)
    managers = sorted({s.manager_id for s in league.snapshots})
    events: dict[str, int] = {}
    for e in final.events:
        events[e.kind] = events.get(e.kind, 0) + 1
    return BacktestReport(
        window=window,
        policy=_policy_line(world, _name(policy, world), first, final, window),
        do_nothing=_policy_line(world, "hold the starting squad", first, held_only, window),
        actual=manager_line(world, league, _us(league), rounds, window),
        rivals=tuple(
            manager_line(world, league, m, rounds, window)
            for m in managers
            if m not in league.self_ids
        ),
        matchdays=tuple(r.day_number for r in rounds),
        events=events,
        violations=final.violations,
    )


def _days(rounds: Sequence[Round]) -> frozenset[int]:
    return frozenset(r.day_number for r in rounds)


# ---------------------------------------------------------------------------
# The ensemble: one configuration judged on many paths, not one draw
# ---------------------------------------------------------------------------

ENSEMBLE_STARTS = tuple(W1_START + PATH_SPACING * i for i in range(15))  # 09-22 → 10-06


@dataclass(frozen=True)
class EnsembleReport:
    name: str
    matchdays: tuple[int, ...]
    money_excess: tuple[int, ...]  # per path: policy minus the rivals' median, same window
    points_excess: tuple[float, ...]
    violations: int
    # Against our real record from the same start: independent of the starting squad.
    money_vs_real: tuple[int, ...] = ()
    points_vs_real: tuple[float, ...] = ()
    pre_reset: bool = True

    @property
    def mean_money(self) -> float:
        return sum(self.money_excess) / len(self.money_excess)

    @property
    def mean_points(self) -> float:
        return sum(self.points_excess) / len(self.points_excess)

    @property
    def mean_money_vs_real(self) -> float:
        return sum(self.money_vs_real) / len(self.money_vs_real)

    @property
    def mean_points_vs_real(self) -> float:
        return sum(self.points_vs_real) / len(self.points_vs_real)

    @property
    def b1(self) -> bool:
        return self.mean_money >= 0

    @property
    def b2(self) -> bool:
        return self.mean_points >= 0

    @property
    def passes(self) -> bool:
        """The bar of ADR-0007: our own record before the reset, the rivals' median from it."""
        if self.violations:
            return False
        if self.pre_reset:
            return self.mean_money_vs_real >= 0 and self.mean_points_vs_real >= 0
        return self.b1 and self.b2


def ensemble(
    world: World,
    league: League,
    policy: Policy,
    starts: Sequence[datetime] = ENSEMBLE_STARTS,
    end: datetime = W1_END,
) -> EnsembleReport:
    world = _with_blends(world, policy)
    reports = [run(world, league, policy, Window(start, end)) for start in starts]
    return EnsembleReport(
        name=reports[0].policy.name,
        matchdays=reports[0].matchdays,
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
        pre_reset=starts[0] < RESET_AT,
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


# ---------------------------------------------------------------------------
# The holdout: a frozen configuration on rounds that kicked off after its freeze
# ---------------------------------------------------------------------------


def holdout_starts(world: World, frozen_at: datetime) -> tuple[datetime, ...]:
    """Session times every PATH_SPACING from the freeze until the first round after it."""
    later = [r.kickoff for r in world.rounds if r.kickoff > frozen_at]
    if not later:
        return ()
    first_kickoff = min(later)
    day = frozen_at.date()
    first = min(
        datetime.combine(day + timedelta(days=d), t, tzinfo=timezone.utc)
        for d in (0, 1)
        for t in SESSION_TIMES_UTC
        if datetime.combine(day + timedelta(days=d), t, tzinfo=timezone.utc) >= frozen_at
    )
    starts = []
    at = first
    while at < first_kickoff:
        starts.append(at)
        at += PATH_SPACING
    return tuple(starts)


def holdout(
    world: World, league: League, policy: Policy, *, frozen_at: datetime, now: datetime
) -> EnsembleReport | None:
    """None until a round that kicked off after the freeze has finished."""
    starts = holdout_starts(world, frozen_at)
    if not starts or not window_rounds(world, Window(starts[0], now)):
        return None
    return ensemble(world, league, policy, starts, now)


def format_ensemble(r: EnsembleReport) -> str:
    bar = "PASS" if r.passes else "FAIL"
    basis = "our real record" if r.pre_reset else "the rivals' median"
    return (
        f"  {r.name[:24]:<24} MD{list(r.matchdays)} paths {len(r.money_excess)}\n"
        f"    vs our real record: money {r.mean_money_vs_real:>+13,.0f} "
        f"[{min(r.money_vs_real):+,} .. {max(r.money_vs_real):+,}]  "
        f"pts/MD {r.mean_points_vs_real:>+7.1f} "
        f"[{min(r.points_vs_real):+.0f} .. {max(r.points_vs_real):+.0f}]\n"
        f"    vs the rivals' median: money {r.mean_money:>+13,.0f}  pts/MD {r.mean_points:>+7.1f}\n"
        f"    violations {r.violations}; bar (ADR-0007, vs {basis}): {bar}"
    )


def format_report(r: BacktestReport) -> list[str]:
    def row(line: Line) -> str:
        return f"  {line.name[:26]:<26} {line.net_worth_change:>+14,}  {line.points_per_matchday:>8.1f}"

    return [
        f"{r.window.start:%Y-%m-%d %H:%M} -> {r.window.end:%Y-%m-%d %H:%M} UTC, "
        f"matchdays {list(r.matchdays)}",
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


# ---------------------------------------------------------------------------
# The leader scorecard (ADR-0012): every change, against the league's top three
# ---------------------------------------------------------------------------

LEADER_COUNT = 3
MONEY_NOISE = 3_000_000  # a mean change smaller than this, in euros, is not a change
POINTS_NOISE = 10.0  # likewise, in points a matchday


def trading_profit(
    buys: Sequence[tuple[datetime, str, int]],
    sells: Sequence[tuple[datetime, str, int]],
    end_value,
) -> int:
    """Each buy's first later sale price (or its value at the end) minus its price."""
    total = 0
    for at, pid, price in buys:
        sale = min((s for s in sells if s[1] == pid and s[0] > at), default=None)
        exit_price = sale[2] if sale else (end_value(pid) or 0)
        total += exit_price - price
    return total


@dataclass(frozen=True)
class Card:
    name: str
    total: float  # mean transfer-only net worth change over the windows
    trading: float  # mean trading profit
    points: float  # mean points a matchday
    short: int = 0  # kickoffs with an empty slot (v3 only)
    kickoffs: int = 0


@dataclass(frozen=True)
class Scorecard:
    v3: Card
    leaders: tuple[Card, ...]  # the top three by total profit, best first

    @property
    def money_level(self) -> str:
        totals = sorted(c.total for c in self.leaders)
        trades = sorted(c.trading for c in self.leaders)
        if self.v3.total > totals[-1] and self.v3.trading > trades[-1]:
            return "beats"
        if self.v3.total >= totals[0] and self.v3.trading >= median(trades):
            return "same level"
        return "below"

    def keeps(self, baseline: Scorecard) -> tuple[bool, list[str]]:
        """Kept only if no axis gets worse (beyond noise) and at least one gets better."""
        v, b = self.v3, baseline.v3
        worse, better = [], []
        if v.short > b.short:
            worse.append(f"empty slots {b.short} -> {v.short}")
        elif v.short < b.short:
            better.append(f"empty slots {b.short} -> {v.short}")
        for label, now, before, noise in (
            ("total profit", v.total, b.total, MONEY_NOISE),
            ("trading profit", v.trading, b.trading, MONEY_NOISE),
            ("points", v.points, b.points, POINTS_NOISE),
        ):
            if now < before - noise:
                worse.append(f"{label} {before:,.0f} -> {now:,.0f}")
            elif now > before + noise:
                better.append(f"{label} {before:,.0f} -> {now:,.0f}")
        return (not worse and bool(better)), [*(f"worse: {w}" for w in worse), *better]


def scorecard(
    world: World,
    league: League,
    policy: Policy,
    starts: Sequence[datetime] = ENSEMBLE_STARTS,
    end: datetime = W1_END,
) -> Scorecard:
    """v3 and the league's top three, over the same windows (one path a start)."""
    world = _with_blends(world, policy)
    end_value = _end_value(world, end)
    totals: list[float] = []
    tradings: list[float] = []
    points: list[float] = []
    short = kickoffs = 0
    managers: dict[str, dict[str, list[float]]] = {}
    for start in starts:
        window = Window(start, end)
        rounds = window_rounds(world, window)
        days = _days(rounds)
        first = start_state(league, start)
        final = engine.run(world, first, start, end, policy)
        played = [m for m in final.matchdays if m.day_number in days]
        totals.append(engine.net_worth(world, final, end) - engine.net_worth(world, first, start))
        tradings.append(
            trading_profit(
                [(e.at, e.player_id, e.amount) for e in final.events if e.kind == "won"],
                [(e.at, e.player_id, e.amount) for e in final.events if e.kind == "sell"],
                end_value,
            )
        )
        points.append(sum(m.points for m in played) / len(played) if played else 0.0)
        short += sum(m.fielded < engine.ELEVEN for m in played)
        kickoffs += len(played)
        for mid in {s.manager_id for s in league.snapshots} - league.self_ids:
            line = manager_line(world, league, mid, rounds, window)
            theirs = [t for t in league.transfers if t.manager_id == mid and start < t.at <= end]
            row = managers.setdefault(mid, {"total": [], "trading": [], "points": []})
            row["total"].append(line.net_worth_change)
            row["points"].append(line.points_per_matchday)
            row["trading"].append(
                trading_profit(
                    [(t.at, t.player_id, t.price) for t in theirs if t.kind == BUY],
                    [(t.at, t.player_id, t.price) for t in theirs if t.kind == SELL],
                    end_value,
                )
            )
    cards = [
        Card(league.names.get(mid, mid), _mean(r["total"]), _mean(r["trading"]), _mean(r["points"]))
        for mid, r in managers.items()
    ]
    leaders = tuple(sorted(cards, key=lambda c: c.total, reverse=True)[:LEADER_COUNT])
    mine = Card(
        _name(policy, world),
        _mean(totals),
        _mean(tradings),
        _mean(points),
        short,
        kickoffs,
    )
    return Scorecard(v3=mine, leaders=leaders)


def format_scorecard(card: Scorecard, baseline: Scorecard | None = None) -> str:
    rows = [f"  {'':<28}{'total':>10}{'trading':>10}{'pts/MD':>8}{'empty slots':>13}"]
    for c in (card.v3, *card.leaders):
        slots = f"{c.short}/{c.kickoffs}" if c is card.v3 else ""
        rows.append(
            f"  {c.name[:28]:<28}{c.total / 1e6:>+9.1f}m{c.trading / 1e6:>+9.1f}m{c.points:>8.0f}{slots:>13}"
        )
    rows.append(f"  money against the leaders: {card.money_level}")
    if baseline is not None:
        kept, why = card.keeps(baseline)
        rows.append(
            f"  against {baseline.v3.name}: {'KEEP' if kept else 'REJECT'} ({'; '.join(why) or 'no change'})"
        )
    return "\n".join(rows)


def _end_value(world: World, end: datetime):
    day = value_day(end)
    return lambda pid: engine.value_at(world, pid, day)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# The configurations worth comparing, on the current code (tags pin the frozen code).
NAMED_POLICIES: dict[str, Policy] = {
    "planner": Policy(mu_scale=1.0, price_blend=True),
    "flip book": Policy(price_blend=True, flipbook=True, planned_debt=True),
    "two books": Policy(
        price_blend=True,
        flipbook=True,
        planned_debt=True,
        core_upgrades=True,
        core_capital_neutral=True,
    ),
    "no empty slot": Policy(
        price_blend=True,
        flipbook=True,
        planned_debt=True,
        core_upgrades=True,
        core_capital_neutral=True,
        core_fills_holes="squad",
    ),
    "core fills its eleven": Policy(
        price_blend=True,
        flipbook=True,
        planned_debt=True,
        core_upgrades=True,
        core_capital_neutral=True,
        core_fills_holes="core",
    ),
    "cheap fill, squad": Policy(
        price_blend=True,
        flipbook=True,
        planned_debt=True,
        core_upgrades=True,
        core_capital_neutral=True,
        core_fills_holes="squad",
        cheap_fill=True,
    ),
    "cheap fill, core": Policy(
        price_blend=True,
        flipbook=True,
        planned_debt=True,
        core_upgrades=True,
        core_capital_neutral=True,
        core_fills_holes="core",
        cheap_fill=True,
    ),
}
