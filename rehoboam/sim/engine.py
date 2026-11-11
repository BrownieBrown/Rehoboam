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

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone

from rehoboam.desk import bids, drift, planner
from rehoboam.desk.eleven import ELEVEN, EMPTY_SLOT_POINTS, best_eleven
from rehoboam.desk.exchange import points_per_euro
from rehoboam.desk.model import Book, Offer, Player
from rehoboam.desk.points import PriceBlend, fit_blend
from rehoboam.sim import auction
from rehoboam.sim.history import Listing, Prediction, Round, Transfer, ValuePaths, value_day

SESSION_TIMES_UTC = (time(8, 0), time(20, 0))
CEILING_LOOKBACK = timedelta(days=120)
VALUE_FALLBACK_DAYS = 7
FORECAST_MOMENTUM = 0.95  # services/mv_forecast's rule: 0.95 × the last move, capped at ±30%
FORECAST_CAP = 0.30
ROUND_SPAN = timedelta(days=4)  # a round's matches are over 4 days after its first whistle


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


@dataclass(frozen=True)
class Policy:
    mu_scale: float = 1.0
    limits: planner.Limits = planner.Limits()
    price_blend: bool = False


@dataclass(frozen=True)
class Pending:
    listing: Listing
    bid: int


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str  # sell | bid | won | lost | declined
    player_id: str
    amount: int


@dataclass(frozen=True)
class Matchday:
    day_number: int
    points: float
    cash_at_kickoff: int
    fielded: int
    expected: float = 0.0  # the eleven's EP when it was picked: expected minus points is the curse


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


def fit_blends(world: World) -> tuple[tuple[datetime, PriceBlend], ...]:
    """One fit per finished round, on every round up to it, usable once it has finished."""
    rows: list[tuple[float, int, float]] = []
    out: list[tuple[datetime, PriceBlend]] = []
    for rnd in world.rounds:
        day = value_day(rnd.kickoff)
        for pid in world.predictions:
            pred = prediction_at(world, pid, rnd.kickoff)
            points = world.points.get((pid, rnd.day_number))
            value = value_at(world, pid, day)
            if pred and pred.day_number == rnd.day_number and points is not None and value:
                rows.append((pred.ep, value, points))
        fit = fit_blend(rows)
        if fit is not None:
            out.append((rnd.kickoff + ROUND_SPAN, fit))
    return tuple(out)


def _drift(world: World, table: drift.DriftTable, player_id: str, day: date) -> float:
    path = world.paths.get(player_id)
    if path is None:
        return 0.0
    features = drift.features_at(path, day, drift.market_falling(world.moves, day))
    return table.predict(features).mean if features else 0.0


def _ceiling(world: World, market_value: int, at: datetime) -> float | None:
    band = drift.band(market_value)
    return bids.ceiling(
        p
        for t, v, p in world.rival_buys
        if at - CEILING_LOOKBACK <= t < at and drift.band(v) == band
    )


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
    event = Event(listing.expires_at, kind, listing.player_id, pending.bid)
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
        _player(world, pid, rnd.kickoff, value_at(world, pid, day) or 0, 0.0) for pid in state.held
    ]
    expected, eleven = best_eleven(p for p in squad if p is not None)
    points = sum(world.points.get((p.player_id, rnd.day_number), 0.0) for p in eleven)
    points += EMPTY_SLOT_POINTS * (ELEVEN - len(eleven))
    violations = state.violations
    if state.cash < 0:
        points = 0.0
        violations = (*violations, f"MD{rnd.day_number}: negative wallet {state.cash:,} at kickoff")
    record = Matchday(rnd.day_number, points, state.cash, len(eleven), expected)
    return replace(state, matchdays=(*state.matchdays, record), violations=violations)


def _player(
    world: World, player_id: str, at: datetime, value: int, drift_mean: float
) -> Player | None:
    position = world.positions.get(player_id)
    if position is None:
        return None
    ep = ep_at(world, player_id, at)
    blend = blend_at(world, at)
    if blend is not None:
        ep = blend.apply(ep, value)
    return Player(player_id, position, value, ep, drift_mean)


def session(world: World, state: SimState, at: datetime, policy: Policy) -> SimState:
    day = value_day(at)
    held = _held_players(world, state, at, day)
    offers = _offers(world, state, at, day)
    book = Book(
        held=tuple(held),
        cash=state.cash,
        committed=sum(p.bid for p in state.pending),
        open_bids=len(state.pending),
    )
    mu = points_per_euro(
        max(1, matchdays_left(world, at)), world.eur_per_point, scale=policy.mu_scale
    )
    result = planner.plan(book, list(offers), mu, policy.limits)
    listing_of = {o.player.player_id: listing for o, listing in offers.items()}
    return _apply(state, at, result.sells, result.bids, listing_of)


def _held_players(world: World, state: SimState, at: datetime, day: date) -> list[Player]:
    out = []
    for pid in sorted(state.held):
        value = value_at(world, pid, day)
        player = _player(world, pid, at, value or 0, _drift(world, world.hold_table, pid, day))
        if player is not None and value:
            out.append(player)
    return out


def _offers(world: World, state: SimState, at: datetime, day: date) -> dict[Offer, Listing]:
    pending = {p.listing.player_id for p in state.pending}
    out: dict[Offer, Listing] = {}
    for listing in world.listings:
        pid = listing.player_id
        if not listing.listed_at <= at < listing.expires_at or pid in state.held or pid in pending:
            continue
        base = _forecast_at_expiry(world, pid, day, listing.expires_at)
        if not base:
            continue
        premium = _ceiling(world, base, at)
        player = _player(world, pid, at, base, _drift(world, world.buy_table, pid, day))
        if premium is None or player is None:
            continue
        out[Offer(player, premium)] = listing
    return out


def _apply(
    state: SimState,
    at: datetime,
    sells: Sequence[Player],
    new_bids: Sequence[Offer],
    listing_of: Mapping[str, Listing],
) -> SimState:
    events = list(state.events)
    events += [Event(at, "sell", p.player_id, p.market_value) for p in sells]
    events += [Event(at, "bid", o.player.player_id, o.bid) for o in new_bids]
    return replace(
        state,
        cash=state.cash + sum(p.market_value for p in sells),
        held=state.held - {p.player_id for p in sells},
        pending=(
            *state.pending,
            *(Pending(listing_of[o.player.player_id], o.bid) for o in new_bids),
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
