"""Kickbase payloads → corpus rows, with no I/O.

Both corpus writers — the SQLite ``TrainingCorpus`` the offline tools read
and the store's ``CorpusStore`` the ingestion writes — persist the same
rows, so the parsing lives once, here. Every rule in this module was
verified against live responses (dates in the docstrings) and must not be
re-derived from field names.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from rehoboam.enrichment.corpus import _to_epoch
from rehoboam.match_parsing import parse_minutes

POSITIONS = {1: "Goalkeeper", 2: "Defender", 3: "Midfielder", 4: "Forward"}


def universe_rows(items: list[dict]) -> list[dict]:
    """Lineup-selection items → ``player_universe`` rows.

    The id is ``pi`` and there is no first-name field on this endpoint
    (measured live, 2026-07-29), so ``first_name`` is always None.
    """
    out = []
    for item in items:
        pid = item.get("pi")
        if pid is None:
            continue
        out.append(
            {
                "player_id": str(pid),
                "first_name": None,
                "last_name": item.get("n"),
                "position": POSITIONS.get(item.get("pos")),
                "team_id": item.get("tid"),
                "market_value": item.get("mv"),
                "average_points": item.get("ap"),
            }
        )
    return out


def match_history_rows(player_id: str, team_id: str | None, performance: dict) -> list[dict]:
    """Performance response → one row per placed match.

    ``pt`` is the player's team for that match and is the primary source for
    home/away and opponent; the caller's ``team_id`` is the *current* team and
    is wrong for every match before the player's last transfer, so it is only
    a fallback. ``ap``/``tp``/``asp`` are season-end totals stamped on every
    row (verified live) and are deliberately not stored — they would leak the
    season outcome into training rows.
    """
    rows: list[dict] = []
    fallback_team = str(team_id) if team_id is not None else None
    for season in performance.get("it") or []:
        title = season.get("ti")
        if not title:
            continue
        for m in season.get("ph") or []:
            day = m.get("day")
            if day is None:
                continue
            t1 = str(m.get("t1", "")) or None
            t2 = str(m.get("t2", "")) or None
            pt = m.get("pt")
            team = str(pt) if pt is not None else fallback_team
            is_home = 1 if team is not None and team == t1 else 0
            st = m.get("st")
            rows.append(
                {
                    "player_id": str(player_id),
                    "season": str(title),
                    "day_number": int(day),
                    "match_date": m.get("md"),
                    "points": int(m.get("p") or 0),
                    "minutes": parse_minutes(m.get("mp")),
                    "team_id": team,
                    "opponent_team_id": t2 if is_home else t1,
                    "is_home": is_home,
                    "status": int(st) if st is not None else None,
                }
            )
    return rows


def mv_series_rows(player_id: str, history: dict) -> list[dict]:
    """Market-value history → rows; ``dt`` is days since epoch; ``mv <= 0`` is a sentinel."""
    return [
        {
            "player_id": str(player_id),
            "snapshot_at": float(item["dt"]) * 86400.0,
            "market_value": int(item["mv"]),
        }
        for item in (history.get("it") or [])
        if item.get("dt") is not None and item.get("mv") and item["mv"] > 0
    ]


def transfer_rows(player_id: str, history: dict) -> list[dict]:
    """Transfer history → rows; ``dt`` here is ISO-8601 text (verified 2026-07-29)."""
    rows: list[dict] = []
    for item in history.get("it") or []:
        dt = item.get("dt")
        if not dt:
            continue
        try:
            transfer_at = _to_epoch(dt)
        except (ValueError, TypeError):
            continue
        counterparty_id = item.get("u")
        rows.append(
            {
                "player_id": str(player_id),
                "transfer_at": transfer_at,
                "price": item.get("trp"),
                "transfer_type": item.get("t"),
                "counterparty_id": (str(counterparty_id) if counterparty_id is not None else None),
                "counterparty_name": item.get("unm"),
            }
        )
    return rows


def _opt_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _opt_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def status_row(player_id: str, day: date, details: dict, fetched_at: float) -> dict:
    """League player details → one ``player_status_daily`` row.

    ``st`` is the injury/availability status (0 healthy), ``prob`` the lineup
    probability (1 starter … 5 unlikely) — the two fields the scorer never had
    day by day. ``tfhmvt`` is the euro change of the last daily market-value
    update, read with the value it produced. ``g``/``a``/``y``/``r``/``sec``/
    ``tp``/``ap`` are this season's goals, assists, yellow cards, red cards,
    seconds played, total points and average points — what Kickbase's own
    player card shows, probed live 2026-09-18 (all null for a player with no
    appearances; ``ap`` is a float). ``pim`` is the player's photo, a
    CDN-relative path (`content/file/<hash>.png`, probed live 2026-09-18) —
    the exact path as Kickbase gives it, so a changed photo is detectable;
    the caller carries it onto ``player_universe.image_source``, it is not a
    ``player_status_daily`` column. Missing fields stay None rather than
    becoming a fake healthy starter or a fake zero season.
    """
    tid = details.get("tid")
    return {
        "player_id": str(player_id),
        "day": day,
        "status": _opt_int(details.get("st")),
        "lineup_probability": _opt_int(details.get("prob")),
        "market_value": _opt_int(details.get("mv")),
        "mv_change": _opt_int(details.get("tfhmvt")),
        "team_id": str(tid) if tid is not None else None,
        "fetched_at": float(fetched_at),
        "goals": _opt_int(details.get("g")),
        "assists": _opt_int(details.get("a")),
        "yellow_cards": _opt_int(details.get("y")),
        "red_cards": _opt_int(details.get("r")),
        "seconds_played": _opt_int(details.get("sec")),
        "season_points": _opt_int(details.get("tp")),
        "season_average": _opt_float(details.get("ap")),
        "image_source": details.get("pim"),
    }


def _iso_epoch(value) -> float | None:
    """ISO `...Z` (or offset) string → epoch seconds; None when unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def market_listing_rows(
    payload: dict, *, snapshot_at: float, our_user_id: str, source: str
) -> list[dict]:
    """`GET /market` → one row per listing. `exs` is seconds until expiry and is
    only present on manager-listed players; `uop` counts as our bid only when
    `uoid` is our user id."""
    rows: list[dict] = []
    for item in (payload or {}).get("it") or []:
        if not isinstance(item, dict) or not item.get("i"):
            continue
        ask = item.get("prc") or item.get("mv")
        if not ask:
            continue  # not actionable without a price
        seller = item.get("u")
        seller_id = str(seller.get("i")) if isinstance(seller, dict) and seller.get("i") else None
        ours = bool(our_user_id) and str(item.get("uoid") or "") == str(our_user_id)
        exs = item.get("exs")
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "player_id": str(item["i"]),
                "ask": int(ask),
                "market_value": _opt_int(item.get("mv")),
                "mv_trend": _opt_int(item.get("mvt")),
                "seller_id": seller_id,
                "offer_count": _opt_int(item.get("ofc")),
                "our_bid": _opt_int(item.get("uop")) if ours else None,
                "listed_at": _iso_epoch(item.get("dt")),
                "expires_at": (snapshot_at + float(exs) if isinstance(exs, int | float) else None),
                "status": _opt_int(item.get("st")),
                "lineup_probability": _opt_int(item.get("prob")),
                "source": source,
            }
        )
    return rows


def manager_squad_rows(
    manager_id: str, items: list, *, snapshot_at: float, source: str
) -> list[dict]:
    """`/managers/{mid}/squad` `it[]` → rows (`pi` id, `mvgl` gain/loss, `iotm` on the
    market)."""
    rows: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        pid = item.get("pi") or item.get("i")
        if not pid:
            continue
        on_market = item.get("iotm")
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "manager_id": str(manager_id),
                "player_id": str(pid),
                "market_value": _opt_int(item.get("mv")),
                "gain_loss": _opt_int(item.get("mvgl")),
                "on_market": bool(on_market) if on_market is not None else None,
                "source": source,
            }
        )
    return rows


def own_squad_rows(
    manager_id: str, players: list, *, snapshot_at: float, source: str
) -> list[dict]:
    """Our own squad from the session's `Player` objects (no gain/loss or market flag
    there)."""
    rows: list[dict] = []
    for p in players or []:
        pid = getattr(p, "id", None)
        if not pid:
            continue
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "manager_id": str(manager_id),
                "player_id": str(pid),
                "market_value": _opt_int(getattr(p, "market_value", None)),
                "gain_loss": None,
                "on_market": None,
                "source": source,
            }
        )
    return rows


def manager_rows(
    ranking: dict, *, league_id: str, our_user_id: str, updated_at: float
) -> list[dict]:
    """`/ranking` `us[]` (older payloads: `it[]`) → managers."""
    rows: list[dict] = []
    for m in (ranking or {}).get("us") or (ranking or {}).get("it") or []:
        if not isinstance(m, dict) or not m.get("i"):
            continue
        rows.append(
            {
                "manager_id": str(m["i"]),
                "league_id": str(league_id),
                "name": str(m.get("n") or m["i"]),
                "is_self": str(m["i"]) == str(our_user_id),
                "updated_at": updated_at,
            }
        )
    return rows


def fixture_rows(schedule: dict, *, season: str, updated_at: float) -> list[dict]:
    """`/competitions/1/matchdays` → one row per fixture with a match id and a
    parseable kickoff."""
    rows: list[dict] = []
    for group in (schedule or {}).get("it") or []:
        if not isinstance(group, dict) or not isinstance(group.get("day"), int):
            continue
        for f in group.get("it") or []:
            if not isinstance(f, dict) or not f.get("mi"):
                continue
            kickoff = _iso_epoch(f.get("dt"))
            if kickoff is None or f.get("t1") is None or f.get("t2") is None:
                continue
            rows.append(
                {
                    "match_id": str(f["mi"]),
                    "season": season,
                    "day_number": int(group["day"]),
                    "kickoff": kickoff,
                    "home_team_id": str(f["t1"]),
                    "away_team_id": str(f["t2"]),
                    "home_goals": _opt_int(f.get("t1g")),
                    "away_goals": _opt_int(f.get("t2g")),
                    "status": int(f.get("st") or 0),
                    "updated_at": updated_at,
                }
            )
    return rows


def league_table_rows(
    table: dict, *, season: str, day_number: int, updated_at: float
) -> list[dict]:
    """`/competitions/1/table` `it[]` → rows (`cpl` place, `pcpl` previous, `cp`
    points, `mc`, `gd`)."""
    rows: list[dict] = []
    for r in (table or {}).get("it") or []:
        if not isinstance(r, dict) or not r.get("tid") or r.get("cpl") is None:
            continue
        rows.append(
            {
                "season": season,
                "day_number": int(day_number),
                "team_id": str(r["tid"]),
                "place": int(r["cpl"]),
                "previous_place": _opt_int(r.get("pcpl")),
                "points": _opt_int(r.get("cp")),
                "played": _opt_int(r.get("mc")),
                "goal_difference": _opt_int(r.get("gd")),
                "updated_at": updated_at,
            }
        )
    return rows


def team_row(profile: dict, *, updated_at: float) -> dict | None:
    """`/teams/{tid}/teamprofile` → one `teams` row; None without an id.

    `tim` is the club crest, a CDN-relative path (`content/file/<hash>.svg`,
    probed live 2026-09-18) — the exact path as Kickbase gives it, so a
    changed crest is detectable; `crest_path` (where our own copy lives) is a
    later task's concern and is not written here.
    """
    if not isinstance(profile, dict) or not profile.get("tid"):
        return None
    return {
        "team_id": str(profile["tid"]),
        "name": str(profile.get("tn") or profile["tid"]),
        "short_name": str(profile["ts"]) if profile.get("ts") else None,
        "updated_at": updated_at,
        "crest_source": profile.get("tim"),
    }
