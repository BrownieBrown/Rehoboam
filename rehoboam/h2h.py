"""Head-to-head matchup awareness for the 26/27 season.

The league moved from total-points to H2H. That changes what winning means:
the target is no longer "score as much as possible" but "score more than one
specific opponent this matchday". A squad that reliably scores 700 beats a
squad that averages 750 with wild swings, if the opponent sits at 690.

This module answers three questions the rest of the bot needs:

- who do we play next, and when
- what is that opponent likely to score
- how far ahead or behind are we

It reads two endpoints that are not otherwise used:

``/v4/leagues/{id}/matchups``
    ``{"mds": [{"day": N, "sd": ..., "ed": ..., "it": [{"u1": {...}, "u2": {...}}]}]}``
    One entry per matchday, each holding every pairing in the league.

``/v4/leagues/{id}/managers/{uid}/squad``
    ``{"nps": 15, "it": [{"pi": id, "pn": name, "pos": 1-4, "ap": avg, "mv": value}]}``
    Any manager's full squad — opponents included, which is what makes a
    projection possible at all.

Projection deliberately uses *average points*, not the fitted EP model. Scoring
an opponent's 15 players through the EP pipeline costs 15 extra performance
fetches per run, and the comparison only has to be fair, not precise — both
sides are projected the same way, so the margin is meaningful even though the
absolute numbers are cruder than our own EP figures. The margin is the output
that matters; treat the totals as indicative.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Kickbase position codes in the manager-squad payload.
POSITION_NAMES = {1: "Goalkeeper", 2: "Defender", 3: "Midfielder", 4: "Forward"}

# A legal eleven needs at least this many of each, same shape the lineup
# optimiser uses. Anything above the minimum is filled by whoever scores most.
FORMATION_MINIMUMS = {"Goalkeeper": 1, "Defender": 3, "Midfielder": 2, "Forward": 1}
LINEUP_SIZE = 11


@dataclass
class Matchup:
    """One H2H fixture involving us."""

    day: int
    starts_at: str
    ends_at: str
    opponent_id: str
    opponent_name: str


@dataclass
class Projection:
    """A projected matchday total for one manager."""

    manager_name: str
    squad_size: int
    projected_points: float
    eleven: list[tuple[str, str, float]] = field(default_factory=list)


@dataclass
class MatchupOutlook:
    """Us against one opponent, projected."""

    matchup: Matchup
    us: Projection
    them: Projection

    @property
    def margin(self) -> float:
        """Positive means we are projected to win."""
        return self.us.projected_points - self.them.projected_points

    @property
    def verdict(self) -> str:
        """Plain words for a margin, sized against a typical ~700-point total."""
        m = self.margin
        if m >= 80:
            return "clear favourite"
        if m >= 25:
            return "favoured"
        if m > -25:
            return "too close to call"
        if m > -80:
            return "underdog"
        return "heavy underdog"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def next_matchup(api, league, *, now: datetime | None = None) -> Matchup | None:
    """Our next unfinished fixture, or None if the schedule is unreadable.

    "Next" is the earliest matchday whose end time has not passed — an
    in-progress matchday is still the one that matters, because that is the
    opponent tonight's lineup is being set against.
    """
    now = now or datetime.now(timezone.utc)
    try:
        me = str(api.user.id)
        payload = api.client.session.get(
            f"{api.client.BASE_URL}/v4/leagues/{league.id}/matchups"
        ).json()
    except Exception:
        logger.warning("h2h: could not read matchups", exc_info=True)
        return None

    best: Matchup | None = None
    best_end: datetime | None = None
    for md in payload.get("mds") or []:
        ends = _parse_iso(md.get("ed"))
        if ends is None or ends < now:
            continue
        for pairing in md.get("it") or []:
            u1, u2 = pairing.get("u1") or {}, pairing.get("u2") or {}
            if str(u1.get("i")) == me:
                opponent = u2
            elif str(u2.get("i")) == me:
                opponent = u1
            else:
                continue
            if best_end is None or ends < best_end:
                best_end = ends
                best = Matchup(
                    day=int(md.get("day") or 0),
                    starts_at=md.get("sd") or "",
                    ends_at=md.get("ed") or "",
                    opponent_id=str(opponent.get("i") or ""),
                    opponent_name=opponent.get("n") or "unknown",
                )
    if best is None:
        logger.info("h2h: no upcoming fixture found for %s", me)
    return best


def manager_squad(api, league, manager_id: str) -> list[dict]:
    """Any manager's squad as raw payload rows. Empty list on failure."""
    try:
        payload = api.client.session.get(
            f"{api.client.BASE_URL}/v4/leagues/{league.id}/managers/{manager_id}/squad"
        ).json()
        return list(payload.get("it") or [])
    except Exception:
        logger.warning("h2h: could not read squad for %s", manager_id, exc_info=True)
        return []


def project_squad(rows: list[dict], manager_name: str) -> Projection:
    """Project a matchday total from a squad payload.

    Picks the best legal eleven by average points: position minimums first,
    then the highest scorers regardless of position. Injured and long-term
    injured players (``st`` 4 and 256) are excluded — they cannot be fielded,
    and counting them would flatter whichever side is carrying injuries.
    """
    OUT = {4, 256}
    available = []
    for row in rows:
        if row.get("st") in OUT:
            continue
        pos = POSITION_NAMES.get(row.get("pos"), "Midfielder")
        available.append((str(row.get("pn") or "?"), pos, float(row.get("ap") or 0.0)))
    available.sort(key=lambda r: -r[2])

    eleven: list[tuple[str, str, float]] = []
    for position, minimum in FORMATION_MINIMUMS.items():
        picks = [p for p in available if p[1] == position][:minimum]
        eleven.extend(picks)
    chosen = {(n, p) for n, p, _ in eleven}
    for row in available:
        if len(eleven) >= LINEUP_SIZE:
            break
        if (row[0], row[1]) not in chosen:
            eleven.append(row)
            chosen.add((row[0], row[1]))

    return Projection(
        manager_name=manager_name,
        squad_size=len(rows),
        projected_points=sum(p[2] for p in eleven),
        eleven=eleven,
    )


def matchup_outlook(api, league, *, now: datetime | None = None) -> MatchupOutlook | None:
    """Us against our next opponent. None when the fixture cannot be read."""
    fixture = next_matchup(api, league, now=now)
    if fixture is None:
        return None

    me = str(api.user.id)
    ours = project_squad(manager_squad(api, league, me), "you")
    theirs = project_squad(manager_squad(api, league, fixture.opponent_id), fixture.opponent_name)
    return MatchupOutlook(matchup=fixture, us=ours, them=theirs)
