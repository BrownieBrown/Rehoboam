"""The live adapter for v3 (ADR-0008 to ADR-0013).

The live session runs `engine.decide`: the same code the replay is judged on. This
module only translates. A `Snapshot` of the real league (squad, wallet, Kickbase's
open listings with their exact expiry, our open bids, each player's book) becomes
the simulator's `World` and `SimState`; the store supplies the history.

`orders` turns a `Decision` into orders, sales first (they fund the bids), each
sale's reason starting with the exit-rule prefix `services/flip_exit` tags;
`execute` runs them one by one through the callables it is given, so a refused
order never stops the rest. The live session (`AutoTrader._run_v3_session`,
`TRADING_MODE=v3`) hands it the real instant sale and the gated buy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

from rehoboam.desk.solvency import DEBT_CAP_OF_SQUAD_VALUE
from rehoboam.sim import backtest, engine
from rehoboam.sim.engine import Decision, Event, Pending, SimState, World
from rehoboam.sim.history import BERLIN, Listing, in_update_window, value_day

FROZEN = "v3-frozen-6"
POLICY = backtest.NAMED_POLICIES["eleven + surplus"]
FIT_CUTOFF = date(2026, 7, 1)  # the drift tables are fitted on 2025/26, as in every replay
DRIFT_HORIZON_DAYS = backtest.HORIZON_DAYS  # reported on a flip's intent; v3 sells at the turn
FLIP = "flip"
CORE = "core"


@dataclass(frozen=True)
class OpenListing:
    """One of Kickbase's own listings, open now."""

    player_id: str
    listed_at: datetime
    expires_at: datetime
    market_value: int
    our_bid: int | None = None


@dataclass(frozen=True)
class Snapshot:
    now: datetime
    cash: int
    squad: Mapping[str, int]  # player id -> market value now
    listings: tuple[OpenListing, ...]
    books: Mapping[str, str]  # player id -> "flip" | "core", for what we hold or bid on
    names: Mapping[str, str] = field(default_factory=dict)


def decision_time(now: datetime) -> datetime:
    """Inside Kickbase's nightly update (22:00-22:30 Berlin) a reading may be either
    side of it; the session then decides as of 21:59 Berlin, on yesterday's values."""
    if not in_update_window(now):
        return now
    berlin = now.astimezone(BERLIN)
    return berlin.replace(hour=21, minute=59, second=0, microsecond=0).astimezone(timezone.utc)


def listings_from_payload(items: Iterable[Mapping], now: datetime) -> tuple[OpenListing, ...]:
    """Kickbase's own listings from the raw market payload (`it`): no seller (`u`),
    an expiry in seconds (`exs`). A manager's listing is never a candidate."""
    out = []
    for item in items:
        if item.get("u") or not item.get("exs") or not item.get("i"):
            continue
        listed = item.get("dt")
        out.append(
            OpenListing(
                player_id=str(item["i"]),
                listed_at=_parse(listed) if listed else now,
                expires_at=now + timedelta(seconds=int(item["exs"])),
                market_value=int(item.get("mv") or 0),
                our_bid=int(item["uop"]) if item.get("uop") else None,
            )
        )
    return tuple(out)


def to_world_and_state(world: World, snap: Snapshot, at: datetime) -> tuple[World, SimState]:
    """The replay's world and state for a live session at ``at``.

    Today's values come from the API where the store has none yet; the open
    listings join the history; money counts to the season's end (ADR-0013). A
    held player is a flip only if his book says so; a bid with no known book
    (placed by hand) is core.
    """
    day = value_day(at)
    open_listings = tuple(
        Listing(lst.player_id, lst.listed_at, lst.expires_at, at, lst.market_value)
        for lst in snap.listings
    )
    current = {**snap.squad, **{lst.player_id: lst.market_value for lst in snap.listings}}
    paths = dict(world.paths)
    for pid, value in current.items():
        path = paths.get(pid, {})
        if value and day not in path:
            paths[pid] = {**path, day: value}
    world = replace(
        world,
        paths=paths,
        listings=(*world.listings, *open_listings),
        reset_at=backtest.SEASON_END,
    )
    by_id = {lst.player_id: lst for lst in open_listings}
    pending = tuple(
        Pending(by_id[lst.player_id], lst.our_bid, snap.books.get(lst.player_id, CORE))
        for lst in snap.listings
        if lst.our_bid
    )
    events = tuple(
        Event(at, "won", pid, 0, FLIP) for pid in sorted(snap.squad) if snap.books.get(pid) == FLIP
    )
    return world, SimState(
        cash=snap.cash, held=frozenset(snap.squad), pending=pending, events=events
    )


def plan(conn, snap: Snapshot) -> tuple[Decision, World, datetime]:
    """Load the history (read-only), then decide as the frozen configuration would."""
    at = decision_time(snap.now)
    world, _ = backtest.load(conn, fit_cutoff=FIT_CUTOFF, until=snap.now)
    world = replace(
        world,
        blends=engine.fit_blends(world, form=POLICY.form_blend),
        blends_form=POLICY.form_blend,
    )
    world, state = to_world_and_state(world, snap, at)
    return engine.decide(world, state, at, POLICY), world, at


def books_from_store(conn, player_ids: Sequence[str]) -> dict[str, str]:
    """Each player's book from our own history: a bid or a purchase marked `flip`."""
    if not player_ids:
        return {}
    rows = conn.execute(
        "select player_id, intent from rehoboam.tracked_purchases where player_id = any(%(ids)s) "
        "union all "
        "select player_id, intent from rehoboam.pending_bids where player_id = any(%(ids)s)",
        {"ids": list(player_ids)},
    ).fetchall()
    return {r["player_id"]: FLIP if r["intent"] == FLIP else CORE for r in rows}


def describe(decision: Decision, snap: Snapshot, world: World) -> list[str]:
    """What the session would do, in words."""
    lines = []
    for p in decision.sells:
        if decision.repaying:
            why = "repay the wallet before the kickoff"
        elif snap.books.get(p.player_id) == FLIP:
            why = "flip: a night without a rise (the turn)"
        else:
            why = "core: not in the eleven"
        lines.append(f"SELL {_name(snap, p.player_id):<28} {p.market_value:>12,}  {why}")
    for o in decision.bids:
        lst = decision.listings[o.player.player_id]
        book = decision.books.get(o.player.player_id, "?")
        lines.append(
            f"BID  {_name(snap, o.player.player_id):<28} {o.bid:>12,}  +{o.premium:.1%} {book:<4} "
            f"EP {o.player.ep_next:5.1f} drift {o.player.drift:+.1%}  "
            f"expires {lst.expires_at:%a %H:%M} UTC"
        )
    return lines or ["nothing to do"]


def _name(snap: Snapshot, player_id: str) -> str:
    return snap.names.get(player_id, player_id)


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


# --- executing a decision ---------------------------------------------------------

# Prefixes `services/flip_exit.exit_rule_tag` reads, so the outcome tables tag v3's sales.
REASON_TURN = "Flip exit at the turn (v3): a night without a rise"
REASON_REPAY = "Debt recovery (v3): the wallet back to zero before the kickoff"
REASON_CORE = "v3 core: not in the eleven"


@dataclass(frozen=True)
class Headroom:
    """What Kickbase still lets the session commit: the gate's copy of `engine.decide`'s
    debt rule (cash net of open bids, `engine.py`; a third of the unsold squad).

    Kickbase refuses an offer (`err 5050 ThirtyThreePercentRuleExceeded`) once the
    wallet, every open offer counted as won, would sit more than
    `DEBT_CAP_OF_SQUAD_VALUE` of team value below zero. Team value is what we hold: a
    sale moves its value into cash, a bid's player joins only when the auction
    settles. This reproduces all 15 offers of the two sessions that hit the error
    (`tests/test_live/test_v3.py`); a third of worth, team value plus wallet, does not.
    """

    cash: int  # the wallet net of every open offer
    squad_value: int

    @classmethod
    def of(cls, *, cash: int, squad_value: int, open_bids: int) -> Headroom:
        return cls(cash=cash - open_bids, squad_value=squad_value)

    @property
    def spendable(self) -> int:
        return self.cash + int(DEBT_CAP_OF_SQUAD_VALUE * self.squad_value)

    def after_sale(self, value: int) -> Headroom:
        return replace(self, cash=self.cash + value, squad_value=self.squad_value - value)

    def after_bid(self, bid: int) -> Headroom:
        return replace(self, cash=self.cash - bid)


@dataclass(frozen=True)
class Order:
    kind: str  # "sell" | "bid"
    player_id: str
    amount: int  # the sale's market value, or the bid
    reason: str
    book: str | None = None  # a bid's book: "core" | "flip"
    drift: float = 0.0  # a bid's expected drift (reported on a flip's intent)
    reference: int = 0  # a bid's base: the value expected when the listing expires


# The furthest one nightly update can move the engine's forecast (`_forecast_at_expiry`).
FORECAST_REACH = engine.FORECAST_MOMENTUM * engine.FORECAST_CAP


def gate_reference(base: int, *, live_value: int) -> int:
    """The value the safety gate measures a v3 bid's premium against.

    The engine prices a bid on the value expected when the listing expires, because
    Kickbase declines a bid below the value then (spec §5, the 2026-10-07 break review);
    for a riser whose listing outlives tonight's update that is above today's value, and
    against today's value the same bid reads as an overpay. The gate therefore judges the
    premium over the engine's base, as long as the base is one tonight's forecast can
    reach from the live value. A base further away is a stale or broken value, and the
    bid is judged on the live value like any other buy.
    """
    if live_value <= 0:
        return live_value
    if abs(base / live_value - 1) <= FORECAST_REACH + 1e-3:
        return base
    return live_value


@dataclass(frozen=True)
class Result:
    order: Order
    ok: bool
    error: str = ""


def orders(decision: Decision, snap: Snapshot) -> list[Order]:
    """The decision as orders: sales first, then the bids, in the planner's order."""
    out: list[Order] = []
    for p in decision.sells:
        if decision.repaying:
            reason = REASON_REPAY
        elif snap.books.get(p.player_id) == FLIP:
            reason = REASON_TURN
        else:
            reason = REASON_CORE
        out.append(Order("sell", p.player_id, p.market_value, reason))
    for o in decision.bids:
        book = decision.books.get(o.player.player_id, CORE)
        reason = (
            f"v3 {book}: +{o.premium:.1%} on {o.player.market_value:,}, drift {o.player.drift:+.1%}"
        )
        out.append(
            Order(
                "bid",
                o.player.player_id,
                o.bid,
                reason,
                book,
                o.player.drift,
                reference=o.player.market_value,
            )
        )
    return out


def execute(orders_: Sequence[Order], *, sell, bid) -> list[Result]:
    """Run every order; an order that fails or raises is reported, never fatal."""
    results: list[Result] = []
    for order in orders_:
        try:
            ok = bool((sell if order.kind == "sell" else bid)(order))
            results.append(Result(order, ok))
        except Exception as e:  # noqa: BLE001 - one refused order must not stop the rest
            results.append(Result(order, False, f"{type(e).__name__}: {e}"))
    return results
