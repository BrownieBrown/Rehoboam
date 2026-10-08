"""Understat's per-player Bundesliga table (xG, xA, shots, key passes …).

Probed 2026-10-08: the JSON that used to be embedded in the league page is
gone; ``POST /main/getPlayersStats/`` with ``league`` and ``season`` (the
start year) answers ``{"success": true, "players": [...]}`` — 367 players for
2026/27 with ``xG``, ``xA``, ``npxG``, ``shots``, ``key_passes``, ``xGChain``,
``xGBuildup`` as strings. One request per refresh. Unofficial endpoint, no key,
so every failure is logged and skipped; nothing in trading reads the table yet
(it is the input the rate model will be fitted against once enough matchdays
are in).
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import date, datetime, timezone
from typing import Any

import requests

from rehoboam.enrichment.names import by_team, match_player

logger = logging.getLogger(__name__)

UNDERSTAT_URL = "https://understat.com/main/getPlayersStats/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

#: Understat `team_title` → Kickbase team id (2026/27 Bundesliga). A club not
#: listed leaves `player_id` null; `rehoboam understat` prints the unmatched.
UNDERSTAT_TEAM_IDS: dict[str, str] = {
    "Augsburg": "13",
    "Bayer Leverkusen": "7",
    "Bayern Munich": "2",
    "Borussia Dortmund": "3",
    "Borussia M.Gladbach": "15",
    "Eintracht Frankfurt": "4",
    "Elversberg": "77",
    "FC Cologne": "28",
    "Freiburg": "5",
    "Hamburger SV": "6",
    "Hoffenheim": "14",
    "Mainz 05": "18",
    "Paderborn": "29",
    "RasenBallsport Leipzig": "43",
    "Schalke 04": "8",
    "Union Berlin": "40",
    "VfB Stuttgart": "9",
    "Werder Bremen": "10",
}


def season_start_year(season: str) -> int:
    """`"2026/2027"` → 2026 (Understat keys a season by its start year)."""
    return int(str(season)[:4])


def fetch_player_stats(
    season: str, *, session: requests.Session | None = None, timeout: float = 20.0
) -> list[dict[str, Any]]:
    """The league's player rows for the season, decoded whether or not the
    server declares its gzip (probed: it compresses without the header)."""
    http = session or requests.Session()
    resp = http.post(
        UNDERSTAT_URL,
        data={"league": "Bundesliga", "season": str(season_start_year(season))},
        headers={
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://understat.com/league/Bundesliga",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    raw = resp.content
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = json.loads(gzip.decompress(raw).decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("success"):
        raise ValueError("understat: unexpected payload")
    players = payload.get("players")
    if not isinstance(players, list):
        raise ValueError("understat: no players in payload")
    return players


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def current_team_title(title: str | None) -> str | None:
    """ "Hamburger SV,Paderborn" lists every club this season; the last is current."""
    if not title:
        return None
    return str(title).split(",")[-1].strip()


def understat_rows(
    players: list[dict[str, Any]],
    *,
    season: str,
    day: date,
    fetched_at: float,
    universe: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Payload → `understat_player_stats` rows, plus the names left unmatched."""
    teams = by_team(universe)
    rows: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for p in players:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        title = current_team_title(p.get("team_title"))
        team_id = UNDERSTAT_TEAM_IDS.get(title or "")
        player_id = match_player(str(p.get("player_name") or ""), team_id, teams)
        if player_id is None:
            unmatched.append(f"{p.get('player_name')} ({title})")
        rows.append(
            {
                "season": season,
                "understat_id": str(p["id"]),
                "day": day,
                "player_name": str(p.get("player_name") or ""),
                "team_title": str(p.get("team_title") or ""),
                "player_id": player_id,
                "position": p.get("position"),
                "games": _int(p.get("games")),
                "time_played": _int(p.get("time")),
                "goals": _int(p.get("goals")),
                "assists": _int(p.get("assists")),
                "shots": _int(p.get("shots")),
                "key_passes": _int(p.get("key_passes")),
                "npg": _int(p.get("npg")),
                "yellow_cards": _int(p.get("yellow_cards")),
                "red_cards": _int(p.get("red_cards")),
                "xg": _float(p.get("xG")),
                "xa": _float(p.get("xA")),
                "npxg": _float(p.get("npxG")),
                "xg_chain": _float(p.get("xGChain")),
                "xg_buildup": _float(p.get("xGBuildup")),
                "fetched_at": float(fetched_at),
            }
        )
    return rows, unmatched


def run_understat_refresh(
    store,
    *,
    season: str,
    now: float,
    stale_after_s: float,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """One snapshot per `stale_after_s`; never raises (the step is logged and skipped)."""
    outcome: dict[str, Any] = {"written": 0, "unmatched": 0, "skipped": None, "error": None}
    try:
        last = store.latest_fetched_at(season)
        if last is not None and now - last < stale_after_s:
            outcome["skipped"] = f"fresh ({(now - last) / 3600:.1f} h old)"
            return outcome
        players = fetch_player_stats(season, session=session)
        day = datetime.fromtimestamp(now, tz=timezone.utc).date()
        rows, unmatched = understat_rows(
            players, season=season, day=day, fetched_at=now, universe=store.universe()
        )
        outcome["written"] = store.write(rows)
        outcome["unmatched"] = len(unmatched)
        if unmatched:
            logger.info(
                "understat: %d names unmatched: %s", len(unmatched), "; ".join(unmatched[:15])
            )
        logger.info("understat: %d rows written for %s", outcome["written"], season)
    except Exception as e:  # noqa: BLE001 -- an outside source never fails the run
        logger.exception("understat refresh failed")
        outcome["error"] = f"{type(e).__name__}: {e}"[:300]
    return outcome


def now_epoch() -> float:
    return time.time()
