"""The next kickoff: the competition schedule first, `/myeleven` as the cross-check.

Probed live 2026-09-15: `/config`'s competition-matchdays endpoint returns
`{day, it: [ {day, mdln, it: [ {dt, st, mi, t1, t2, ...} ]} ]}` and, unlike
`/myeleven`, never blanks out between matchdays -- it lists the whole
schedule, future rounds included, with `st == 0` marking a fixture that
hasn't kicked off yet. That makes it the primary source: `/myeleven` only
carries fixtures for players currently on our squad, so it goes empty
whenever the squad doesn't happen to include anyone from the next round yet
(day 1 of a new season, a squad rebuilt after selling everyone at a
position, etc). The schedule has no such gap.

Both readers are pure -- no API calls, no clock reads -- so `Trader` is the
only place that touches the network or `datetime.now()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _parse_match_date(value) -> datetime | None:
    """Parse a Kickbase match date from either an epoch number or an ISO string.

    Returns None for anything unparseable so callers can keep collecting
    candidates instead of aborting on one bad entry.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        return None
    return None


def next_kickoff_from_matchdays(payload: dict, now: datetime) -> datetime | None:
    """Earliest not-yet-started fixture in the competition schedule.

    `payload["it"]` is a list of matchday groups, each with its own `it` list
    of fixtures. `st == 0` means not started; anything else (2 observed live,
    presumably also covering in-progress states) is excluded so a fixture
    already underway or finished never gets reported as "next". Tolerant of
    missing keys and unparseable dates -- a schema drift here should degrade
    to "the schedule has nothing to say", not raise.
    """
    if not isinstance(payload, dict):
        return None

    candidates: list[datetime] = []
    for day_group in payload.get("it") or []:
        if not isinstance(day_group, dict):
            continue
        for fixture in day_group.get("it") or []:
            if not isinstance(fixture, dict):
                continue
            if fixture.get("st") != 0:
                continue
            parsed = _parse_match_date(fixture.get("dt"))
            if parsed is not None and parsed > now:
                candidates.append(parsed)

    return min(candidates) if candidates else None


def fixtures_from_myeleven(payload: dict) -> list[datetime]:
    """All fixture dates readable from a `/myeleven` payload.

    Kickbase has moved this value between aliases across seasons. Up to
    2025/26 it was a single `nm`/`nextMatch`; as of 2026/27 the response
    carries no such key and the fixture date is instead attached per player
    under `md`, split across `lp` (the set lineup) and `nlp` (everyone
    else). Both shapes are read, newest first, so a fresh season and an old
    one both come back with something.
    """
    if not isinstance(payload, dict):
        return []

    candidates: list[datetime] = []

    legacy = payload.get("nm") or payload.get("nextMatch")
    parsed = _parse_match_date(legacy)
    if parsed is not None:
        candidates.append(parsed)

    # lp[] is the set lineup, nlp[] everyone else -- read BOTH. Before a
    # lineup is set lp[] is empty and nlp[] holds the whole squad;
    # afterwards the starters move to lp[], and reading only nlp[] would
    # time the guard off the bench's fixtures.
    for key in ("lp", "nlp"):
        for entry in payload.get(key) or []:
            if isinstance(entry, dict):
                parsed = _parse_match_date(entry.get("md"))
                if parsed is not None:
                    candidates.append(parsed)

    return candidates


@dataclass(frozen=True)
class NextKickoff:
    """One composed answer, for both the phase computation and session facts.

    `source` is `"schedule"`, `"myeleven"` or `"none"`. `cross_check` is
    whatever the *other* source answered (None when that source had
    nothing), kept around so a disagreement is visible rather than silently
    picking one number.
    """

    at: datetime | None
    source: str
    cross_check: datetime | None
    matchday_in_progress: bool
