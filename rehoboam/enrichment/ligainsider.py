"""ligainsider's "Voraussichtliche Aufstellung" — an outside predicted eleven.

Probed 2026-10-08: the club page (`/fc-bayern-muenchen/1/`) answers a plain
GET with a browser user agent (kicker.de does not: 403 on every page). The
pitch is `player_position_row` divs holding `player_position_column` divs; the
first `player_name` in a column is the predicted starter, any further one the
alternative the editors list beside him. "Gegen <opponent> fehlen" lists the
players out for the match, "Letzte Aktualisierung" when the page last changed.

This is a scraper, so it is held to the scraper's rules: a parse that does not
find eleven starters writes nothing for that club, every failure is logged and
the run continues, and the scorer reads the result only where Kickbase's own
code is undecided (`lineup_prob.effective_lineup_code`), behind
`PREDICTED_XI_ENABLED`, once its accuracy has been read off `fit-lineup-prob`.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from rehoboam.enrichment.names import by_team, match_player
from rehoboam.store.lineup_store import LIGAINSIDER

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ligainsider.de"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

#: Kickbase team id → ligainsider club path (2026/27 Bundesliga, probed 2026-10-08).
LIGAINSIDER_TEAM_PATHS: dict[str, str] = {
    "2": "/fc-bayern-muenchen/1/",
    "3": "/borussia-dortmund/14/",
    "4": "/eintracht-frankfurt/3/",
    "5": "/sc-freiburg/18/",
    "6": "/hamburger-sv/9/",
    "7": "/bayer-04-leverkusen/4/",
    "8": "/fc-schalke-04/13/",
    "9": "/vfb-stuttgart/12/",
    "10": "/sv-werder-bremen/2/",
    "13": "/fc-augsburg/21/",
    "14": "/tsg-hoffenheim/10/",
    "15": "/borussia-moenchengladbach/5/",
    "18": "/1-fsv-mainz-05/17/",
    "28": "/1-fc-koeln/15/",
    "29": "/sc-paderborn-07/1249/",
    "40": "/1-fc-union-berlin/1246/",
    "43": "/rb-leipzig/1311/",
    "77": "/sv-07-elversberg/1331/",
}

#: Slots: starters 1–11 in pitch order, alternatives from 101, missing players None.
ALTERNATIVE_SLOT_BASE = 100

_HEADING = re.compile(r"<h1>\s*VORAUSSICHTLICHE AUFSTELLUNG\s*</h1>", re.I)
_LEGEND = re.compile(r"Spieler stand in der Startelf", re.I)
_COLUMN_SPLIT = re.compile(r'<div class="player_position_(?:row|column)[^"]*">')
_IS_COLUMN = re.compile(r'^<div class="player_position_column')
_NAME = re.compile(r'class="player_name"[^>]*>\s*<a href="/([^"/]+)/"[^>]*>(.*?)</a>', re.S)
_OPPONENT = re.compile(r"gegen\s*<strong>(.*?)</strong>", re.S | re.I)
_UPDATED = re.compile(r"Letzte Aktualisierung:\s*(?:<[^>]+>\s*)*([^<]+)")
_MISSING_HEAD = re.compile(r"<h3[^>]*>\s*Gegen\b[^<]*\bfehlen\s*</h3>", re.I)
_MISSING_NAME = re.compile(r'<a href="/([^"/]+)/"[^>]*>\s*([^<]{2,60}?)\s*</a>')


def _clean(text: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", text)).split())


@dataclass(frozen=True)
class Named:
    name: str
    slug: str  # e.g. "manuel-neuer_114"


@dataclass(frozen=True)
class ParsedLineup:
    starters: tuple[Named, ...]
    alternatives: tuple[Named, ...]
    missing: tuple[Named, ...]
    opponent: str | None
    updated_at: str | None

    @property
    def complete(self) -> bool:
        return len(self.starters) == 11


def parse_team_page(page: str) -> ParsedLineup:
    """The predicted eleven, the alternatives and the missing players on a club page."""
    head = _HEADING.search(page)
    if not head:
        return ParsedLineup((), (), (), None, None)
    tail = page[head.end() :]
    legend = _LEGEND.search(tail)
    pitch = tail[: legend.start()] if legend else tail
    after = tail[legend.end() :] if legend else ""

    starters: list[Named] = []
    alternatives: list[Named] = []
    parts = _COLUMN_SPLIT.split(pitch)
    openings = _COLUMN_SPLIT.findall(pitch)
    for opening, body in zip(openings, parts[1:], strict=False):
        if not _IS_COLUMN.match(opening):
            continue
        names = [Named(_clean(n), slug) for slug, n in _NAME.findall(body)]
        names = [n for n in names if n.name]
        if not names:
            continue
        starters.append(names[0])
        alternatives.extend(names[1:])

    opponent_match = _OPPONENT.search(tail[:5000])
    opponent = _clean(opponent_match.group(1)) if opponent_match else None
    updated_match = _UPDATED.search(after[:4000]) or _UPDATED.search(tail)
    updated_at = _clean(updated_match.group(1)) if updated_match else None

    missing: list[Named] = []
    miss_head = _MISSING_HEAD.search(after)
    if miss_head:
        block = after[miss_head.end() : miss_head.end() + 20000]
        stop = re.search(r"Ergebnisse und n", block, re.I)
        block = block[: stop.start()] if stop else block
        seen = {n.slug for n in starters} | {n.slug for n in alternatives}
        for slug, name in _MISSING_NAME.findall(block):
            if "_" not in slug or slug in seen:
                continue
            seen.add(slug)
            missing.append(Named(_clean(name), slug))

    return ParsedLineup(tuple(starters), tuple(alternatives), tuple(missing), opponent, updated_at)


def slug_name(slug: str) -> str:
    """ "fabio-silva_9698" → "fabio silva"."""
    return slug.rsplit("_", 1)[0].replace("-", " ")


def fetch_team_page(
    path: str, *, session: requests.Session | None = None, timeout: float = 20.0
) -> str:
    http = session or requests.Session()
    resp = http.get(
        BASE_URL + path,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def lineup_rows(
    team_id: str,
    parsed: ParsedLineup,
    *,
    season: str,
    day_number: int,
    fetched_at: float,
    universe: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """A club's parsed page → `predicted_lineups` rows, plus the names left unmatched."""
    teams = by_team(universe)
    rows: list[dict[str, Any]] = []
    unmatched: list[str] = []

    def add(named: Named, in_xi: bool, slot: int | None) -> None:
        # The display name first ("Silva"); then the page's own slug, which
        # carries the full name ("fabio-silva_9698") and resolves a first
        # name the display shortened to ("Kaishu" → "kaishu-sano").
        player_id = match_player(named.name, team_id, teams)
        if player_id is None:
            player_id = match_player(slug_name(named.slug), team_id, teams)
        if player_id is None:
            unmatched.append(f"{named.name} ({team_id})")
        rows.append(
            {
                "source": LIGAINSIDER,
                "season": season,
                "day_number": day_number,
                "team_id": str(team_id),
                "player_name": named.name,
                "player_id": player_id,
                "in_xi": in_xi,
                "slot": slot,
                "source_updated_at": parsed.updated_at,
                "fetched_at": float(fetched_at),
            }
        )

    for i, named in enumerate(parsed.starters, start=1):
        add(named, True, i)
    for i, named in enumerate(parsed.alternatives, start=ALTERNATIVE_SLOT_BASE + 1):
        add(named, False, i)
    for named in parsed.missing:
        add(named, False, None)
    return rows, unmatched


def run_predicted_xi_refresh(
    store,
    *,
    now: float,
    days_before: float,
    throttle_seconds: float = 1.5,
    session: requests.Session | None = None,
    team_paths: dict[str, str] | None = None,
    sleep=time.sleep,
) -> dict[str, Any]:
    """Fetch every club's predicted eleven for the upcoming matchday when its
    first kickoff is within `days_before` days. Never raises."""
    outcome: dict[str, Any] = {
        "matchday": None,
        "teams": 0,
        "written": 0,
        "unmatched": 0,
        "incomplete": [],
        "failed": [],
        "skipped": None,
        "error": None,
    }
    try:
        md = store.upcoming_matchday(now)
        if md is None:
            outcome["skipped"] = "no upcoming matchday in fixtures"
            return outcome
        outcome["matchday"] = int(md["day_number"])
        hours_to_kickoff = (float(md["first_kickoff"]) - now) / 3600.0
        if hours_to_kickoff > days_before * 24.0:
            outcome["skipped"] = f"kickoff in {hours_to_kickoff / 24:.1f} d (> {days_before} d)"
            return outcome
        universe = store.universe() if hasattr(store, "universe") else _universe(store)
        rows: list[dict[str, Any]] = []
        paths = team_paths if team_paths is not None else LIGAINSIDER_TEAM_PATHS
        for team_id, path in paths.items():
            try:
                parsed = parse_team_page(fetch_team_page(path, session=session))
            except Exception as e:  # noqa: BLE001 -- one club's failure costs one club
                logger.warning("ligainsider: %s failed: %s", path, e)
                outcome["failed"].append(team_id)
                continue
            finally:
                if throttle_seconds:
                    sleep(throttle_seconds)
            if not parsed.complete:
                logger.warning(
                    "ligainsider: %s has %d starters, not written", path, len(parsed.starters)
                )
                outcome["incomplete"].append(team_id)
                continue
            team_rows, unmatched = lineup_rows(
                team_id,
                parsed,
                season=str(md["season"]),
                day_number=int(md["day_number"]),
                fetched_at=now,
                universe=universe,
            )
            if unmatched:
                logger.info("ligainsider: unmatched on %s: %s", team_id, "; ".join(unmatched))
            outcome["unmatched"] += len(unmatched)
            outcome["teams"] += 1
            rows.extend(team_rows)
        outcome["written"] = store.write(rows)
        logger.info(
            "ligainsider: MD%s %d clubs, %d rows, %d unmatched, failed=%s incomplete=%s",
            outcome["matchday"],
            outcome["teams"],
            outcome["written"],
            outcome["unmatched"],
            outcome["failed"],
            outcome["incomplete"],
        )
    except Exception as e:  # noqa: BLE001 -- an outside source never fails the run
        logger.exception("predicted xi refresh failed")
        outcome["error"] = f"{type(e).__name__}: {e}"[:300]
    return outcome


def _universe(store) -> list[dict[str, Any]]:
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT player_id, first_name, last_name, team_id FROM rehoboam.player_universe"
        ).fetchall()
    return [dict(r) for r in rows]
