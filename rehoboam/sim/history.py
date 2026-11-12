"""The history the simulator replays: value paths, Kickbase listings, transfers.

Read-only, one query per kind. Every timestamp comes back as an aware UTC
datetime; every market-value day is a `date`, keyed the way `mv_series` keys it
(the point sits at 00:00 UTC of its day).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
PUBLISH_OFFSET = timedelta(hours=22)
BUY = 1
SELL = 2

ValuePaths = Mapping[str, Mapping[date, int]]


def value_day(at: datetime) -> date:
    """The `mv_series` day whose value is in force at ``at``.

    Kickbase publishes the new values at 22:00 Berlin, so a moment before 22:00
    is priced against yesterday's point. This is the same rule as migration 024's
    `transfer_premiums` (Berlin time minus 22 hours). Keep the two identical.
    """
    if at.tzinfo is None:
        raise ValueError("value_day needs an aware datetime")
    return (at.astimezone(BERLIN) - PUBLISH_OFFSET).date()


def _utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


@dataclass(frozen=True)
class Listing:
    """One Kickbase listing, as the last session before its expiry saw it."""

    player_id: str
    listed_at: datetime
    expires_at: datetime
    last_seen: datetime
    market_value: int  # at last_seen


@dataclass(frozen=True)
class Transfer:
    manager_id: str
    player_id: str
    at: datetime
    kind: int  # BUY or SELL
    price: int


@dataclass(frozen=True)
class SquadSnapshot:
    """A manager's squad as Kickbase reported it, with each player's market value."""

    manager_id: str
    at: datetime
    players: Mapping[str, int]


@dataclass(frozen=True)
class WalletReading:
    at: datetime
    budget: int


def load_squad_snapshots(conn) -> tuple[SquadSnapshot, ...]:
    rows = conn.execute(
        "select snapshot_at, manager_id, player_id, market_value from rehoboam.manager_squads "
        "order by manager_id, snapshot_at"
    ).fetchall()
    grouped: dict[tuple[str, float], dict[str, int]] = defaultdict(dict)
    for row in rows:
        grouped[(row["manager_id"], row["snapshot_at"])][row["player_id"]] = int(
            row["market_value"] or 0
        )
    return tuple(
        SquadSnapshot(manager_id=manager, at=_utc(at), players=players)
        for (manager, at), players in sorted(
            grouped.items(), key=lambda item: (item[0][0], item[0][1])
        )
    )


def load_wallet(conn) -> tuple[WalletReading, ...]:
    """Our own budget readings (rivals' cash is not visible on Kickbase)."""
    rows = conn.execute(
        "select snapshot_at, budget from rehoboam.team_value_history "
        "where budget is not null order by snapshot_at"
    ).fetchall()
    return tuple(WalletReading(at=_utc(r["snapshot_at"]), budget=int(r["budget"])) for r in rows)


def in_update_window(at: datetime) -> bool:
    """True from 22:00 to 22:30 Berlin, while Kickbase's nightly update lands.

    A reading in this window may hold either side of the update, so it is
    used for neither (the same window as `services/mv_forecast.reading_window`).
    """
    local = at.astimezone(BERLIN)
    return local.hour == 22 and local.minute < 30


def load_value_paths(conn) -> ValuePaths:
    """Daily market values: `mv_series`, gaps filled from `player_status_daily`.

    `mv_series` is refreshed weekly per player, so its newest days are thin.
    `player_status_daily` is read before every session; keyed by `value_day`
    of its fetch time it matched `mv_series` on all 24,230 overlapping readings
    (2026-11-11). `mv_series` wins any conflict.
    """
    paths: dict[str, dict[date, int]] = defaultdict(dict)
    status = conn.execute(
        "select player_id, fetched_at, market_value from rehoboam.player_status_daily "
        "where market_value > 0 and fetched_at is not null"
    ).fetchall()
    for row in status:
        at = _utc(row["fetched_at"])
        if not in_update_window(at):
            paths[row["player_id"]][value_day(at)] = int(row["market_value"])
    series = conn.execute(
        "select player_id, snapshot_at, market_value from rehoboam.mv_series "
        "where market_value > 0"
    ).fetchall()
    for row in series:
        paths[row["player_id"]][_utc(row["snapshot_at"]).date()] = int(row["market_value"])
    return dict(paths)


def load_kickbase_listings(conn, *, expired_before: datetime) -> tuple[Listing, ...]:
    """Every Kickbase listing (no manager seller) that expired before the cutoff."""
    rows = conn.execute(
        """
        select distinct on (player_id, listed_at)
               player_id, listed_at, expires_at, snapshot_at, market_value
        from rehoboam.market_listings
        where (seller_id is null or seller_id = '')
          and snapshot_at < expires_at
          and expires_at < %(before)s
        order by player_id, listed_at, snapshot_at desc
        """,
        {"before": expired_before.timestamp()},
    ).fetchall()
    return tuple(
        Listing(
            player_id=row["player_id"],
            listed_at=_utc(row["listed_at"]),
            expires_at=_utc(row["expires_at"]),
            last_seen=_utc(row["snapshot_at"]),
            market_value=int(row["market_value"]),
        )
        for row in rows
    )


def load_transfers(conn, *, since: datetime) -> tuple[Transfer, ...]:
    rows = conn.execute(
        """
        select manager_id, player_id, transfer_dt::timestamptz as at,
               transfer_type, transfer_price
        from rehoboam.manager_transfers
        where transfer_dt::timestamptz >= %(since)s
        order by transfer_dt::timestamptz
        """,
        {"since": since},
    ).fetchall()
    return tuple(
        Transfer(
            manager_id=row["manager_id"],
            player_id=row["player_id"],
            at=row["at"].astimezone(timezone.utc),
            kind=int(row["transfer_type"]),
            price=int(row["transfer_price"] or 0),
        )
        for row in rows
    )


@dataclass(frozen=True)
class Prediction:
    at: datetime
    ep: float
    day_number: int
    p_appear: float | None = None  # the scorer's chance he starts or comes on (status 5 or 4)


@dataclass(frozen=True)
class Round:
    day_number: int
    kickoff: datetime  # the round's first whistle: lineup and wallet lock here


def load_predictions(
    conn, *, since: datetime
) -> tuple[dict[str, tuple[Prediction, ...]], dict[str, str]]:
    """The live sessions' predictions per player, oldest first, and each player's position."""
    rows = conn.execute(
        """
        select player_id, predicted_at, predicted_ep, position, day_number, p_status
        from rehoboam.predictions
        where app = 'function' and not backfill and predicted_at >= %(since)s
          and predicted_ep is not null
        order by predicted_at
        """,
        {"since": since.timestamp()},
    ).fetchall()
    by_player: dict[str, list[Prediction]] = defaultdict(list)
    positions: dict[str, str] = {}
    for r in rows:
        by_player[r["player_id"]].append(
            Prediction(
                _utc(r["predicted_at"]),
                float(r["predicted_ep"]),
                int(r["day_number"]),
                appearance_probability(r["p_status"]),
            )
        )
        if r["position"]:
            positions[r["player_id"]] = r["position"]
    return {pid: tuple(ps) for pid, ps in by_player.items()}, positions


STARTED, CAME_ON = "5", "4"  # the scorer's played statuses (p_status keys)


def appearance_probability(p_status: Mapping[str, float] | None) -> float | None:
    """P(he starts or comes on) from the scorer's status distribution; None if unknown."""
    if not p_status:
        return None
    return float(p_status.get(STARTED, 0.0)) + float(p_status.get(CAME_ON, 0.0))


def load_points(conn, *, season: str) -> dict[tuple[str, int], float]:
    rows = conn.execute(
        "select player_id, day_number, sum(points) as points from rehoboam.player_match_history "
        "where season = %(season)s group by 1, 2",
        {"season": season},
    ).fetchall()
    return {(r["player_id"], int(r["day_number"])): float(r["points"] or 0) for r in rows}


MATCH_LENGTH = timedelta(hours=2)  # kickoff to final whistle, with stoppage time
MATCH_SEASONS = ("2025/2026", "2026/2027")  # the seasons the value paths cover


def load_match_points(conn) -> dict[str, tuple[tuple[date, int], ...]]:
    """Per player, (value day the result is known, points), oldest first.

    The value day of the final whistle (kickoff + `MATCH_LENGTH`) under the
    22:00 Berlin rule. A future fixture's placeholder row is harmless: a reader
    only looks at matches known before its own day.
    """
    rows = conn.execute(
        "select player_id, match_date, points from rehoboam.player_match_history "
        "where season = any(%(seasons)s) and match_date is not null",
        {"seasons": list(MATCH_SEASONS)},
    ).fetchall()
    out: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for r in rows:
        kickoff = datetime.fromisoformat(r["match_date"].replace("Z", "+00:00"))
        out[r["player_id"]].append((value_day(kickoff + MATCH_LENGTH), int(r["points"] or 0)))
    return {pid: tuple(sorted(matches)) for pid, matches in out.items()}


def load_appearances(conn, *, season: str) -> dict[str, tuple[tuple[date, int], ...]]:
    """Per player, this season's appearances (minutes > 0): (value day known, points)."""
    rows = conn.execute(
        "select player_id, match_date, points from rehoboam.player_match_history "
        "where season = %(season)s and match_date is not null and minutes > 0",
        {"season": season},
    ).fetchall()
    out: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for r in rows:
        kickoff = datetime.fromisoformat(r["match_date"].replace("Z", "+00:00"))
        out[r["player_id"]].append((value_day(kickoff + MATCH_LENGTH), int(r["points"] or 0)))
    return {pid: tuple(sorted(apps)) for pid, apps in out.items()}


def load_rounds(conn, *, season: str) -> tuple[Round, ...]:
    rows = conn.execute(
        "select day_number, min(kickoff) as kickoff from rehoboam.fixtures "
        "where season = %(season)s and kickoff is not null group by 1 order by 2",
        {"season": season},
    ).fetchall()
    return tuple(Round(int(r["day_number"]), _utc(r["kickoff"])) for r in rows)


def load_matchday_points(conn) -> dict[tuple[str, int], float]:
    """Each manager's points per matchday, from the newest ranking snapshot of that day."""
    rows = conn.execute(
        """
        select distinct on (manager_id, day_number) manager_id, day_number, matchday_points
        from rehoboam.league_rank_history
        where matchday_points is not null
        order by manager_id, day_number, snapshot_at desc
        """
    ).fetchall()
    return {(r["manager_id"], int(r["day_number"])): float(r["matchday_points"]) for r in rows}


def load_manager_names(conn) -> dict[str, str]:
    return {
        r["manager_id"]: r["name"]
        for r in conn.execute("select manager_id, name from rehoboam.managers")
    }


def load_self_ids(conn) -> frozenset[str]:
    rows = conn.execute("select manager_id from rehoboam.managers where is_self").fetchall()
    return frozenset(row["manager_id"] for row in rows)


def load_league_premiums(conn, *, season: str) -> tuple[tuple[int, float], ...]:
    """(market value, premium as a fraction) of every rival buy in the season."""
    rows = conn.execute(
        """
        select mv_at_transfer, premium_pct
        from rehoboam.transfer_premiums
        where not is_self and season = %(season)s
          and premium_pct is not null and mv_at_transfer > 0
        """,
        {"season": season},
    ).fetchall()
    return tuple((int(r["mv_at_transfer"]), float(r["premium_pct"]) / 100.0) for r in rows)
