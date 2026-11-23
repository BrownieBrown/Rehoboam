"""The backtest engine: the `desk` planner replayed session by session (spec v3 §8).

Each session sees only what a live session could have known:

- the value in force (`value_day`);
- the newest live prediction made before it;
- tonight's forecast as the bid's base;
- the band ceilings from rival buys before it;
- drift tables fitted before the window.

Between sessions, events run in time order:

- **auctions** settle against the league's real buyer of that listing (`sim.auction`);
- **kickoffs** field the best eleven by expected points and score it on the real
  points of that matchday.

A negative wallet at kickoff scores zero and is recorded as a violation.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from rehoboam.desk import bids, core, drift, flipbook, planner, solvency
from rehoboam.desk import kickoff as kickoff_plan
from rehoboam.desk.eleven import ELEVEN, EMPTY_SLOT_POINTS, best_eleven
from rehoboam.desk.exchange import points_per_euro
from rehoboam.desk.model import MAX_SQUAD, Book, Offer, Player
from rehoboam.desk.points import FORM_MIN_APPS, PriceBlend, fit_blend, fit_form_blend
from rehoboam.sim import auction
from rehoboam.sim.history import Listing, Prediction, Round, Transfer, ValuePaths, value_day

SESSION_TIMES_UTC = (time(8, 0), time(20, 0))
CEILING_LOOKBACK = timedelta(days=120)
VALUE_FALLBACK_DAYS = 7
FORECAST_MOMENTUM = 0.95  # services/mv_forecast's rule: 0.95 × the last move, capped at ±30%
FORECAST_CAP = 0.30
ROUND_SPAN = timedelta(days=4)  # a round's matches are over 4 days after its first whistle
SESSION_GAP = timedelta(hours=12)
COMMIT_SPAN = timedelta(days=7)  # the drift horizon a bid's premium paid for (M2-14)
UNKNOWN_DRIFT_LOW = -0.20  # a named sale with no drift estimate is valued 20% down


@dataclass(frozen=True)
class World:
    """Everything the replay reads, loaded once."""

    paths: ValuePaths
    moves: Mapping[date, float]
    hold_table: drift.DriftTable  # lag 0: a holding's path starts now
    buy_table: drift.DriftTable  # lag 1: a listing's path starts after it settles
    listings: tuple[Listing, ...]
    rivals: Mapping[tuple[str, datetime], auction.Rival]
    predictions: Mapping[str, tuple[Prediction, ...]]
    positions: Mapping[str, str]
    points: Mapping[tuple[str, int], float]
    rounds: tuple[Round, ...]
    rival_buys: tuple[tuple[datetime, int, float], ...]  # (at, value, premium), ours excluded
    eur_per_point: float
    reset_at: datetime
    # Walk-forward price blends: (usable from, fit). Empty: raw EP (spec §4.1).
    blends: tuple[tuple[datetime, PriceBlend], ...] = ()
    # Per player, (value day the result is known, points): the LAST_POINTS refinement.
    matches: Mapping[str, drift.Matches] = field(default_factory=dict)
    # Per player, this season's appearances (minutes > 0): (value day known, points).
    appearances: Mapping[str, drift.Matches] = field(default_factory=dict)
    blends_form: bool = False  # the blends carry the form term (desk/points.py)
    # Every player's EP is min(blended EP, real form x P(appear)) -- held, bid on, listed and
    # at the kickoff alike (`Policy.pessimistic_all`, set by backtest._with_blends).
    pessimistic: bool = False


@dataclass(frozen=True)
class Policy:
    mu_scale: float = 1.0
    limits: planner.Limits = planner.Limits()
    price_blend: bool = False
    planned_debt: bool = False  # debt the bench covers, repaid by the last session before kickoff
    # M2-14: a player we won inside the drift horizon, still expected to rise, is
    # not sold to fund a swap: the bid paid a premium for the whole horizon.
    commit_hold: bool = False
    # M2-15: bid argmax F(p)·(drift − p) on the band's rival premiums, not the median.
    value_bid: bool = False
    # M2-17: the flip book instead of the planner (desk/flipbook.py): keep the
    # eleven, sell the bench, buy risers into the free slots, sell at the turn.
    flipbook: bool = False
    flip_by_euros: bool = False  # M2-18: rank flips by expected euros, not percent
    core_upgrades: bool = False  # ADR-0008: the core bids first for one points upgrade
    core_holds_value: bool = False  # M2-20: only upgrades whose drift covers the premium
    core_capital_neutral: bool = False  # M2-21: an upgrade costs at most what it displaces
    core_spends_surplus: bool = False  # M2-22: ...plus the wallet's positive cash, never debt
    flip_debt_covered: bool = False  # M2-23: flip debt <= what the flips raise at their p10
    # M2-23: the core may fill an empty slot outside capital neutrality: "" never, "core" a
    # hole in the core's own eleven (converts flip slots), "squad" a hole the whole squad has.
    core_fills_holes: str = ""
    cheap_fill: bool = False  # M2-25: fill a hole with the most points per euro
    # 2026-11-11: the price blend also reads real form (points per appearance) against EP
    form_blend: bool = False
    # 2026-11-13: one kickoff plan (desk/kickoff.py) instead of the two books' core rules
    # and the repayment after the fact; everything outside it exits at the turn.
    kickoff_plan: bool = False
    kickoff_max_upgrades: int | None = None  # buys that fill no hole, a session (None: any)
    kickoff_min_gain: float = 10.0  # EP an upgrade must add (desk/kickoff.MIN_GAIN)
    kickoff_exit_p10: bool = True  # stock counts at its p10 in the kickoff budget, else at value
    kickoff_capital_neutral: bool = False  # an upgrade costs at most what it moves out of T
    kickoff_named_at_deadline: bool = (
        False  # a sale named for the budget waits for the last session
    )
    # 2026-11-18: a listing is judged on min(blended EP, real form x P(appear)). Picking the
    # highest of noisy estimates picks the overestimated ones (the optimizer's curse): on day 0
    # the eleven scored 97-135 points a matchday below its EP, on W1 (held players) about 0.
    pessimistic_buys: bool = False
    # The same estimate for every player, so a listing and the player it would replace are
    # judged alike (`World.pessimistic`); `pessimistic_buys` alone discounts only listings.
    pessimistic_all: bool = False


@dataclass(frozen=True)
class Pending:
    listing: Listing
    bid: int
    book: str = ""  # "flip" | "core" (ADR-0008); "" for the planner


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str  # sell | bid | won | lost | declined
    player_id: str
    amount: int
    book: str = ""  # the bid's book, carried to its outcome (ADR-0008)


@dataclass(frozen=True)
class Matchday:
    day_number: int
    points: float
    cash_at_kickoff: int
    fielded: int
    expected: float = 0.0  # the eleven's EP when it was picked: expected minus points is the curse
    eleven: tuple[str, ...] = ()  # who was fielded
    squad: tuple[str, ...] = ()  # everyone held at the kickoff


@dataclass(frozen=True)
class SimState:
    cash: int
    held: frozenset[str]
    pending: tuple[Pending, ...] = ()
    events: tuple[Event, ...] = ()
    matchdays: tuple[Matchday, ...] = ()
    violations: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def value_at(world: World, player_id: str, day: date) -> int | None:
    path = world.paths.get(player_id, {})
    for back in range(VALUE_FALLBACK_DAYS + 1):
        value = path.get(day - timedelta(days=back))
        if value:
            return value
    return None


def prediction_at(world: World, player_id: str, at: datetime) -> Prediction | None:
    preds = world.predictions.get(player_id, ())
    i = bisect_right([p.at for p in preds], at)
    return preds[i - 1] if i else None


def ep_at(world: World, player_id: str, at: datetime) -> float:
    pred = prediction_at(world, player_id, at)
    return pred.ep if pred else 0.0


def blend_at(world: World, at: datetime) -> PriceBlend | None:
    usable = [fit for since, fit in world.blends if since <= at]
    return usable[-1] if usable else None


def form_at(world: World, player_id: str, day: date) -> float | None:
    """This season's points per appearance from the matches known before ``day``;
    None below `FORM_MIN_APPS` appearances."""
    apps = world.appearances.get(player_id, ())
    known = bisect_left(apps, (day,))
    if known < FORM_MIN_APPS:
        return None
    return sum(points for _, points in apps[:known]) / known


def _expected_form(world: World, player_id: str, day: date, pred: Prediction) -> float | None:
    """Real form at the scorer's own chance of playing: points per appearance x P(appears).
    The scorer's per-player quality is fitted on past seasons; its availability is not."""
    form = form_at(world, player_id, day)
    if form is None or pred.p_appear is None:
        return None
    return form * pred.p_appear


def fit_blends(world: World, *, form: bool = False) -> tuple[tuple[datetime, PriceBlend], ...]:
    """One fit per finished round, on every round up to it, usable once it has finished.

    With ``form``, each row carries the player's form as known at that round's
    kickoff, and the blend learns how far it corrects the scorer."""
    rows: list[tuple[float, int, float | None, float]] = []
    out: list[tuple[datetime, PriceBlend]] = []
    for rnd in world.rounds:
        day = value_day(rnd.kickoff)
        for pid in world.predictions:
            pred = prediction_at(world, pid, rnd.kickoff)
            points = world.points.get((pid, rnd.day_number))
            value = value_at(world, pid, day)
            if pred and pred.day_number == rnd.day_number and points is not None and value:
                rows.append(
                    (
                        pred.ep,
                        value,
                        _expected_form(world, pid, day, pred) if form else None,
                        points,
                    )
                )
        fit = fit_form_blend(rows) if form else fit_blend([(e, v, p) for e, v, _, p in rows])
        if fit is not None:
            out.append((rnd.kickoff + ROUND_SPAN, fit))
    return tuple(out)


def _drift(world: World, table: drift.DriftTable, player_id: str, day: date) -> float:
    return _drift_estimate(world, table, player_id, day)[0]


def _drift_estimate(
    world: World, table: drift.DriftTable, player_id: str, day: date
) -> tuple[float, float]:
    """(mean, p10); (0, UNKNOWN_DRIFT_LOW) without three nights of history."""
    path = world.paths.get(player_id)
    played = world.matches.get(player_id, ())
    points = drift.last_points_at(played, day) if played else None
    features = (
        drift.features_at(path, day, drift.market_falling(world.moves, day), points)
        if path
        else None
    )
    if features is None:
        return 0.0, UNKNOWN_DRIFT_LOW
    estimate = table.predict(features)
    low = estimate.low if estimate.low == estimate.low else UNKNOWN_DRIFT_LOW  # NaN guard
    return estimate.mean, low


def _rival_premiums(world: World, market_value: int, at: datetime) -> list[float]:
    band = drift.band(market_value)
    return [
        p
        for t, v, p in world.rival_buys
        if at - CEILING_LOOKBACK <= t < at and drift.band(v) == band
    ]


def _ceiling(world: World, market_value: int, at: datetime) -> float | None:
    return bids.ceiling(_rival_premiums(world, market_value, at))


def _forecast_at_expiry(
    world: World, player_id: str, day: date, expires_at: datetime
) -> int | None:
    today = value_at(world, player_id, day)
    yesterday = value_at(world, player_id, day - timedelta(days=1))
    if not today:
        return None
    if value_day(expires_at) <= day or not yesterday:
        return today
    move = max(-FORECAST_CAP, min(FORECAST_CAP, today / yesterday - 1))
    return round(today * (1 + FORECAST_MOMENTUM * move))


def matchdays_left(world: World, at: datetime) -> int:
    return sum(1 for r in world.rounds if at < r.kickoff < world.reset_at)


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


def session_times(start: datetime, end: datetime) -> list[datetime]:
    out = []
    day = start.date()
    while day <= end.date():
        for t in SESSION_TIMES_UTC:
            at = datetime.combine(day, t, tzinfo=timezone.utc)
            if start <= at <= end:
                out.append(at)
        day += timedelta(days=1)
    return out


def run(world: World, state: SimState, start: datetime, end: datetime, policy: Policy) -> SimState:
    last = start
    for at in session_times(start, end):
        state = advance(world, state, last, at)
        state = session(world, state, at, policy)
        last = at
    return advance(world, state, last, end)


def advance(world: World, state: SimState, after: datetime, until: datetime) -> SimState:
    """Settle the auctions and play the kickoffs in (after, until], in time order."""
    events: list[tuple[datetime, int, object]] = [
        (p.listing.expires_at, 0, p) for p in state.pending if after < p.listing.expires_at <= until
    ]
    events += [(r.kickoff, 1, r) for r in world.rounds if after < r.kickoff <= until]
    for _, _, item in sorted(events, key=lambda e: (e[0], e[1])):
        if isinstance(item, Pending):
            state = settle(world, state, item)
        elif isinstance(item, Round):
            state = kickoff(world, state, item)
    return state


def settle(world: World, state: SimState, pending: Pending) -> SimState:
    listing = pending.listing
    remaining = tuple(p for p in state.pending if p is not pending)
    actual = value_at(world, listing.player_id, value_day(listing.expires_at))
    rival = world.rivals.get((listing.player_id, listing.listed_at), auction.NO_RIVAL)
    if actual is None or pending.bid < actual:
        kind = "declined"
        won = False
    else:
        won = auction.wins(pending.bid / actual - 1, rival) is True
        kind = "won" if won else "lost"
    event = Event(listing.expires_at, kind, listing.player_id, pending.bid, pending.book)
    if not won:
        return replace(state, pending=remaining, events=(*state.events, event))
    return replace(
        state,
        cash=state.cash - pending.bid,
        held=state.held | {listing.player_id},
        pending=remaining,
        events=(*state.events, event),
    )


def kickoff(world: World, state: SimState, rnd: Round) -> SimState:
    day = value_day(rnd.kickoff)
    squad = [
        _player(world, pid, rnd.kickoff, value_at(world, pid, day) or 0, 0.0)
        for pid in sorted(state.held)
    ]
    expected, eleven = best_eleven(p for p in squad if p is not None)
    points = sum(world.points.get((p.player_id, rnd.day_number), 0.0) for p in eleven)
    points += EMPTY_SLOT_POINTS * (ELEVEN - len(eleven))
    violations = state.violations
    if state.cash < 0:
        points = 0.0
        violations = (*violations, f"MD{rnd.day_number}: negative wallet {state.cash:,} at kickoff")
    record = Matchday(
        rnd.day_number,
        points,
        state.cash,
        len(eleven),
        expected,
        eleven=tuple(p.player_id for p in eleven),
        squad=tuple(sorted(state.held)),
    )
    return replace(state, matchdays=(*state.matchdays, record), violations=violations)


def _player(
    world: World,
    player_id: str,
    at: datetime,
    value: int,
    drift_mean: float,
    drift_low: float = 0.0,
) -> Player | None:
    position = world.positions.get(player_id)
    if position is None:
        return None
    ep = ep_at(world, player_id, at)
    blend = blend_at(world, at)
    if blend is not None:
        pred = prediction_at(world, player_id, at) if blend.form_weight else None
        form = _expected_form(world, player_id, value_day(at), pred) if pred else None
        ep = blend.apply(ep, value, form)
    if world.pessimistic:
        ep = _form_floor(world, player_id, at, ep)
    return Player(player_id, position, value, ep, drift_mean, drift_low)


def _form_floor(world: World, player_id: str, at: datetime, ep: float) -> float:
    """The lower of ``ep`` and real form at the scorer's chance of appearing; ``ep``
    while the player has too few appearances for a form."""
    pred = prediction_at(world, player_id, at)
    form = _expected_form(world, player_id, value_day(at), pred) if pred else None
    return ep if form is None else min(ep, form)


def session(world: World, state: SimState, at: datetime, policy: Policy) -> SimState:
    if policy.flipbook or policy.kickoff_plan:
        return _flipbook_session(world, state, at, policy)
    day = value_day(at)
    held = _held_players(world, state, at, day, commit=policy.commit_hold)
    offers = _offers(
        world, state, at, day, value_bid=policy.value_bid, pessimistic=policy.pessimistic_buys
    )
    kickoff_at = next_kickoff(world, at)
    book = Book(
        held=tuple(held),
        cash=state.cash,
        committed=sum(p.bid for p in state.pending),
        open_bids=len(state.pending),
        last_before_kickoff=(
            not policy.planned_debt or kickoff_at is None or kickoff_at <= at + SESSION_GAP
        ),
        open_bid_cover=_open_bid_cover(world, state, day, kickoff_at),
    )
    mu = points_per_euro(
        max(1, matchdays_left(world, at)), world.eur_per_point, scale=policy.mu_scale
    )
    result = planner.plan(book, list(offers), mu, policy.limits)
    listing_of = {o.player.player_id: listing for o, listing in offers.items()}
    return _apply(state, at, result.sells, result.bids, listing_of)


def _sellable(listing: Listing, kickoff_at: datetime | None) -> bool:
    """Settles at least one session gap before the kickoff, so a session can sell it."""
    return kickoff_at is not None and listing.expires_at <= kickoff_at - SESSION_GAP


def _open_bid_cover(
    world: World, state: SimState, day: date, kickoff_at: datetime | None, book: str | None = None
) -> int:
    """The p10 resale of the open bids that settle in time (M2-13); one book's with ``book``."""
    cover = 0
    for pending in state.pending:
        if not _sellable(pending.listing, kickoff_at):
            continue
        if book is not None and (pending.book or "flip") != book:
            continue
        pid = pending.listing.player_id
        value = value_at(world, pid, day) or 0
        _, low = _drift_estimate(world, world.buy_table, pid, day)
        cover += round(value * (1 + min(0.0, low)))
    return cover


@dataclass(frozen=True)
class Decision:
    """What one two-book session does: the replay applies it, the live session executes it."""

    sells: tuple[Player, ...]
    bids: tuple[Offer, ...]
    books: Mapping[str, str]  # player id -> "core" | "flip", for every bid
    listings: Mapping[str, Listing]  # player id -> the listing each bid is for
    repaying: bool  # the last session before a kickoff: no bids, the wallet back to zero


def _flipbook_session(world: World, state: SimState, at: datetime, policy: Policy) -> SimState:
    d = decide(world, state, at, policy)
    return _apply(state, at, d.sells, d.bids, d.listings, d.books)


def decide(world: World, state: SimState, at: datetime, policy: Policy) -> Decision:
    """One session of the two books (ADR-0008, M2-17). The core is what we did not
    win as a flip: its best eleven stays, the rest is sold, and with
    `Policy.core_upgrades` it bids first for one points upgrade. The flip book
    exits at the turn, buys into what is left, and repays before the kickoff.

    Pure over (world, state): the replay and the live session call the same code."""
    if policy.kickoff_plan:
        return _decide_kickoff(world, state, at, policy)
    day = value_day(at)
    held = _held_players(world, state, at, day)
    book_of: dict[str, str] = {}
    for e in state.events:  # the latest win decides: a fact from our own history
        if e.kind == "won":
            book_of[e.player_id] = e.book
    flips = [p for p in held if book_of.get(p.player_id, "core") != "core"]
    rest = [p for p in held if book_of.get(p.player_id, "core") == "core"]
    eleven = {p.player_id for p in best_eleven(rest)[1]}
    core_players = [p for p in rest if p.player_id in eleven]
    moves = {p.player_id: _last_move(world, p.player_id, day) for p in flips}
    sells = [*(p for p in rest if p.player_id not in eleven), *flipbook.exits(flips, moves)]
    cash = state.cash + sum(p.market_value for p in sells) - sum(x.bid for x in state.pending)
    kickoff_at = next_kickoff(world, at)
    offers: dict[Offer, Listing] = {}
    new_bids: tuple[Offer, ...] = ()
    books: dict[str, str] = {}
    if kickoff_at is None or kickoff_at <= at + SESSION_GAP:
        sold = {p.player_id for p in sells}
        keep = [p for p in flips if p.player_id not in sold]
        sells += flipbook.repay(keep, core_players, cash=cash, last_moves=moves)
    else:
        offers = _offers(
            world, state, at, day, value_bid=policy.value_bid, pessimistic=policy.pessimistic_buys
        )
        sold = {p.player_id for p in sells}
        squad_value = sum(p.market_value for p in held if p.player_id not in sold)
        allowance = solvency.DEBT_CAP_OF_SQUAD_VALUE * squad_value if policy.planned_debt else 0
        slots = MAX_SQUAD - (len(held) - len(sells)) - len(state.pending)
        candidates = list(offers)
        if policy.core_upgrades and slots > 0:
            mu = points_per_euro(
                max(1, matchdays_left(world, at)), world.eur_per_point, scale=policy.mu_scale
            )
            rules: dict[str, Any] = {
                "holds_value": policy.core_holds_value,
                "capital_neutral": policy.core_capital_neutral,
                "surplus": max(0, cash) if policy.core_spends_surplus else 0,
                "fill_holes": bool(policy.core_fills_holes),
                "cheap_fill": policy.cheap_fill,
            }
            squad_wide = policy.core_fills_holes == "squad"
            others = [p for p in flips if p.player_id not in sold] if squad_wide else []
            if policy.core_fills_holes:
                pending_core, pending_flips = _pending_players(world, state, at, day)
                picks = core.bids(
                    core_players,
                    candidates,
                    mu,
                    cash=cash,
                    allowance=allowance,
                    others=[*others, *pending_flips] if squad_wide else others,
                    pending=pending_core,
                    **rules,
                )[:slots]
            else:
                up = core.upgrade(
                    core_players,
                    candidates,
                    mu,
                    cash=cash,
                    allowance=allowance,
                    others=others,
                    **rules,
                )
                picks = (up,) if up is not None else ()
            for up in picks:
                new_bids, cash, slots = (*new_bids, up), cash - up.bid, slots - 1
                books[up.player.player_id] = "core"
                candidates = [o for o in candidates if o is not up]
        cover = None
        if policy.flip_debt_covered:
            cover = sum(
                p.market_value * (1 + min(0.0, p.drift_low))
                for p in flips
                if p.player_id not in sold
            )
            cover += _open_bid_cover(world, state, day, kickoff_at, book="flip")
        flips_bought = flipbook.entries(
            candidates, cash, slots, allowance, by_euros=policy.flip_by_euros, cover=cover
        )
        books |= {o.player.player_id: "flip" for o in flips_bought}
        new_bids += flips_bought
    chosen = {o.player.player_id for o in new_bids}
    listing_of = {
        o.player.player_id: lst for o, lst in offers.items() if o.player.player_id in chosen
    }
    return Decision(
        sells=tuple(sells),
        bids=tuple(new_bids),
        books=books,
        listings=listing_of,
        repaying=kickoff_at is None or kickoff_at <= at + SESSION_GAP,
    )


def _decide_kickoff(world: World, state: SimState, at: datetime, policy: Policy) -> Decision:
    """One session of the kickoff plan (desk/kickoff.py).

    T, the eleven for the next kickoff, is planned with its sales named against the
    kickoff budget; everything outside T is stock and exits by one rule: at the
    first session that sees a night without a rise, or in the last session before
    the kickoff as far as the wallet needs. The flip entries spend what the budget
    has left after T. Open bids count as won for money, as lost for fielding."""
    day = value_day(at)
    held = _held_players(world, state, at, day)
    held_ids = {p.player_id for p in held}
    kickoff_at = next_kickoff(world, at)
    repaying = kickoff_at is None or kickoff_at <= at + SESSION_GAP
    moves = {p.player_id: _last_move(world, p.player_id, day) for p in held}
    cash = state.cash - sum(x.bid for x in state.pending)
    mu = points_per_euro(
        max(1, matchdays_left(world, at)), world.eur_per_point, scale=policy.mu_scale
    )
    allowance = solvency.DEBT_CAP_OF_SQUAD_VALUE * sum(p.market_value for p in held)
    pending, pending_exit, arriving = _pending_squad(world, state, at, day, kickoff_at)
    squad = [*held, *pending]
    # Selling now raises the value in force; a sale left for later is counted at its p10.
    exit_value = {
        p.player_id: (
            p.market_value
            if repaying or not policy.kickoff_exit_p10
            else p.market_value * (1 + min(0.0, p.drift_low))
        )
        for p in held
    } | ({} if repaying else pending_exit)

    def fieldable(p: Player) -> bool:
        return p.player_id in held_ids or p.player_id in arriving

    if repaying:
        k = kickoff_plan.plan(
            squad,
            (),
            mu,
            cash=cash,
            exit_value=exit_value,
            fieldable=fieldable,
            slots=0,
            allowance=allowance,
        )
        sells = _repay_from_stock(k, held_ids, moves, cash)
        return Decision(sells=sells, bids=(), books={}, listings={}, repaying=True)

    offers = _offers(
        world, state, at, day, value_bid=policy.value_bid, pessimistic=policy.pessimistic_buys
    )
    free = MAX_SQUAD - len(held) - len(state.pending)
    k = kickoff_plan.plan(
        squad,
        [o for o in offers if o.arrives_before_kickoff],
        mu,
        cash=cash,
        exit_value=exit_value,
        fieldable=fieldable,
        slots=max(0, MAX_SQUAD - len(state.pending) - min(len(held), ELEVEN)),
        allowance=allowance,
        max_upgrades=policy.kickoff_max_upgrades,
        min_gain=policy.kickoff_min_gain,
        capital_neutral=policy.kickoff_capital_neutral,
    )
    stock = [p for p in k.stock if p.player_id in held_ids]
    named = {p.player_id for p in k.named} if policy.kickoff_named_at_deadline else set()
    sells = _turn_exits(held, [p for p in stock if p.player_id not in named], moves)
    short = len(k.buys) - (free + len(sells))  # T's buys need room: the weakest stock goes
    if short > 0:
        sold = {p.player_id for p in sells}
        rest = sorted(
            (p for p in stock if p.player_id not in sold and p.player_id not in named),
            key=lambda p: (moves.get(p.player_id) or 0.0, p.drift, p.player_id),
        )
        sells = (*sells, *rest[:short])
    sold = {p.player_id for p in sells}
    cash_after = cash + sum(p.market_value for p in sells) - sum(o.bid for o in k.buys)
    bought = {o.player.player_id for o in k.buys}
    cover = sum(exit_value[p.player_id] for p in stock if p.player_id not in sold)
    cover += sum(v for pid, v in pending_exit.items() if pid not in {p.player_id for p in k.keep})
    slots = MAX_SQUAD - (len(held) - len(sells)) - len(state.pending) - len(k.buys)
    flips = flipbook.entries(
        [o for o in offers if o.player.player_id not in bought],
        cash_after,
        max(0, slots),
        allowance,
        by_euros=policy.flip_by_euros,
        cover=cover,
    )
    new_bids = (*k.buys, *flips)
    chosen = {o.player.player_id for o in new_bids}
    return Decision(
        sells=tuple(sells),
        bids=new_bids,
        books={**dict.fromkeys(bought, "core"), **{o.player.player_id: "flip" for o in flips}},
        listings={
            o.player.player_id: lst for o, lst in offers.items() if o.player.player_id in chosen
        },
        repaying=False,
    )


def _pending_squad(
    world: World, state: SimState, at: datetime, day: date, kickoff_at: datetime | None
) -> tuple[list[Player], dict[str, float], set[str]]:
    """Our open bids' players counted as won: (players, exit value, those who can play
    the kickoff). One that settles too late to be sold raises nothing before it."""
    players: list[Player] = []
    exits: dict[str, float] = {}
    arriving: set[str] = set()
    for pending in state.pending:
        pid = pending.listing.player_id
        mean, low = _drift_estimate(world, world.buy_table, pid, day)
        player = _player(world, pid, at, value_at(world, pid, day) or pending.bid, mean, low)
        if player is None:
            continue
        players.append(player)
        sellable = _sellable(pending.listing, kickoff_at)
        exits[pid] = player.market_value * (1 + min(0.0, low)) if sellable else 0.0
        if kickoff_at is None or pending.listing.expires_at < kickoff_at:
            arriving.add(pid)
    return players, exits, arriving


def _turn_exits(
    held: Sequence[Player], stock: Sequence[Player], moves: Mapping[str, float | None]
) -> tuple[Player, ...]:
    """Stock whose last night was not a rise, unless selling him leaves a slot empty
    in the eleven we hold now (an open bid may yet be lost: ADR-0003)."""
    out: list[Player] = []
    remaining = list(held)
    for p in sorted(stock, key=lambda p: p.player_id):
        move = moves.get(p.player_id)
        if move is None or move > 0:
            continue
        without = [q for q in remaining if q.player_id != p.player_id]
        if len(best_eleven(without)[1]) < len(best_eleven(remaining)[1]):
            continue
        out.append(p)
        remaining = without
    return tuple(out)


def _repay_from_stock(
    k: kickoff_plan.KickoffPlan,
    held_ids: set[str],
    moves: Mapping[str, float | None],
    cash: int,
) -> tuple[Player, ...]:
    """The last session: stock that turned goes; then stock by the least drift until
    the wallet is at zero; T only if all the stock cannot cover it."""
    stock = [p for p in k.stock if p.player_id in held_ids]
    turned = [p for p in stock if (m := moves.get(p.player_id)) is not None and m <= 0]
    sells = list(turned)
    cash += sum(p.market_value for p in turned)
    for p in sorted((p for p in stock if p not in turned), key=lambda p: (p.drift, p.player_id)):
        if cash >= 0:
            break
        sells.append(p)
        cash += p.market_value
    if cash < 0:
        sells += flipbook.repay(
            (), [p for p in k.keep if p.player_id in held_ids], cash=cash, last_moves=moves
        )
    return tuple(sells)


def _pending_players(
    world: World, state: SimState, at: datetime, day: date
) -> tuple[list[Player], list[Player]]:
    """Our open bids' players, counted as won: (core bids, flip bids)."""
    core_side: list[Player] = []
    flip_side: list[Player] = []
    for pending in state.pending:
        pid = pending.listing.player_id
        player = _player(world, pid, at, value_at(world, pid, day) or pending.bid, 0.0)
        if player is not None:
            (core_side if pending.book == "core" else flip_side).append(player)
    return core_side, flip_side


def _last_move(world: World, player_id: str, day: date) -> float | None:
    path = world.paths.get(player_id, {})
    today, yesterday = path.get(day), path.get(day - timedelta(days=1))
    return today / yesterday - 1 if today and yesterday else None


def _held_players(
    world: World, state: SimState, at: datetime, day: date, *, commit: bool = False
) -> list[Player]:
    won_at = {e.player_id: e.at for e in state.events if e.kind == "won"} if commit else {}
    out = []
    for pid in sorted(state.held):
        value = value_at(world, pid, day)
        mean_drift, low = _drift_estimate(world, world.hold_table, pid, day)
        player = _player(world, pid, at, value or 0, mean_drift, low)
        if player is None or not value:
            continue
        bought = won_at.get(pid)
        if bought is not None and at - bought < COMMIT_SPAN and mean_drift >= 0:
            player = replace(player, committed=True)
        out.append(player)
    return out


def next_kickoff(world: World, at: datetime) -> datetime | None:
    later = [r.kickoff for r in world.rounds if r.kickoff > at]
    return min(later) if later else None


def _pessimistic(world: World, player: Player, at: datetime) -> Player:
    """A listing judged on the lower of its blended EP and its real form at the scorer's
    chance of appearing (`Policy.pessimistic_buys`). No form yet keeps the EP."""
    floor = _form_floor(world, player.player_id, at, player.ep_next)
    return player if floor >= player.ep_next else replace(player, ep_next=floor)


def _offers(
    world: World,
    state: SimState,
    at: datetime,
    day: date,
    *,
    value_bid: bool = False,
    pessimistic: bool = False,
) -> dict[Offer, Listing]:
    pending = {p.listing.player_id for p in state.pending}
    kickoff_at = next_kickoff(world, at)
    out: dict[Offer, Listing] = {}
    for listing in world.listings:
        pid = listing.player_id
        if not listing.listed_at <= at < listing.expires_at or pid in state.held or pid in pending:
            continue
        base = _forecast_at_expiry(world, pid, day, listing.expires_at)
        if not base:
            continue
        expected = _drift(world, world.buy_table, pid, day)
        premium = (
            bids.value_premium(_rival_premiums(world, base, at), expected)
            if value_bid
            else _ceiling(world, base, at)
        )
        player = _player(world, pid, at, base, expected)
        if premium is None or player is None:
            continue
        if pessimistic:
            player = _pessimistic(world, player, at)
        arrives = kickoff_at is None or listing.expires_at < kickoff_at
        out[
            Offer(
                player,
                premium,
                arrives_before_kickoff=arrives,
                sellable_before_kickoff=_sellable(listing, kickoff_at),
            )
        ] = listing
    return out


def _apply(
    state: SimState,
    at: datetime,
    sells: Sequence[Player],
    new_bids: Sequence[Offer],
    listing_of: Mapping[str, Listing],
    books: Mapping[str, str] | None = None,
) -> SimState:
    book = books or {}
    events = list(state.events)
    events += [Event(at, "sell", p.player_id, p.market_value) for p in sells]
    events += [
        Event(at, "bid", o.player.player_id, o.bid, book.get(o.player.player_id, ""))
        for o in new_bids
    ]
    return replace(
        state,
        cash=state.cash + sum(p.market_value for p in sells),
        held=state.held - {p.player_id for p in sells},
        pending=(
            *state.pending,
            *(
                Pending(listing_of[o.player.player_id], o.bid, book.get(o.player.player_id, ""))
                for o in new_bids
            ),
        ),
        events=tuple(events),
    )


def net_worth(world: World, state: SimState, at: datetime) -> int:
    day = value_day(at)
    return state.cash + sum(value_at(world, pid, day) or 0 for pid in state.held)


def rival_buys(transfers: Iterable[Transfer], paths: ValuePaths, self_ids: frozenset[str]) -> tuple:
    """(at, value, premium) for every rival buy, priced like `transfer_premiums`."""
    out = []
    for t in transfers:
        if t.kind != 1 or t.price <= 0 or t.manager_id in self_ids:
            continue
        value = paths.get(t.player_id, {}).get(value_day(t.at))
        if value:
            out.append((t.at, value, t.price / value - 1))
    return tuple(out)
