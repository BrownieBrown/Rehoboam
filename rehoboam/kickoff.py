"""The next kickoff: the competition schedule first, `/myeleven` as the cross-check.

Probed live 2026-09-15: `GET /v4/competitions/1/matchdays` returns
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


@dataclass(frozen=True)
class NextFixture:
    day_number: int | None
    at: datetime


@dataclass(frozen=True)
class FinishedMatchday:
    day_number: int
    first_kickoff: datetime
    last_kickoff: datetime


def _day_groups(payload) -> list[tuple[int | None, list[dict]]]:
    """`(day, fixtures)` per matchday group; tolerant of every missing key."""
    if not isinstance(payload, dict):
        return []
    out: list[tuple[int | None, list[dict]]] = []
    for group in payload.get("it") or []:
        if not isinstance(group, dict):
            continue
        day = group.get("day")
        fixtures = [f for f in (group.get("it") or []) if isinstance(f, dict)]
        out.append((int(day) if isinstance(day, int) else None, fixtures))
    return out


def next_fixture_from_matchdays(payload: dict, now: datetime) -> NextFixture | None:
    """Earliest not-yet-started fixture, with the matchday number of its group.

    `st == 0` means not started; anything else (2 observed live, presumably
    also covering in-progress states) is excluded so a fixture already
    underway or finished never gets reported as "next". Tolerant of missing
    keys and unparseable dates -- a schema drift here should degrade to "the
    schedule has nothing to say", not raise.
    """
    best: NextFixture | None = None
    for day, fixtures in _day_groups(payload):
        for fixture in fixtures:
            if fixture.get("st") != 0:
                continue
            parsed = _parse_match_date(fixture.get("dt"))
            if parsed is None or parsed <= now:
                continue
            if best is None or parsed < best.at:
                best = NextFixture(day_number=day, at=parsed)
    return best


def next_kickoff_from_matchdays(payload: dict, now: datetime) -> datetime | None:
    """Earliest not-yet-started fixture in the competition schedule.

    `payload["it"]` is a list of matchday groups, each with its own `it` list
    of fixtures. Delegates to `next_fixture_from_matchdays` and drops the
    matchday number -- kept for callers that only ever needed the timestamp.
    """
    nf = next_fixture_from_matchdays(payload, now)
    return nf.at if nf else None


def finished_matchdays(payload) -> list[FinishedMatchday]:
    """Matchday groups whose every fixture has `st == 2`, oldest first.

    A group with no fixtures, an unparseable date or a missing day number is
    not finished -- it is unknown, and unknown never triggers a report.
    """
    out: list[FinishedMatchday] = []
    for day, fixtures in _day_groups(payload):
        if day is None or not fixtures:
            continue
        if any(f.get("st") != 2 for f in fixtures):
            continue
        dates = [_parse_match_date(f.get("dt")) for f in fixtures]
        if any(d is None for d in dates):
            continue
        out.append(
            FinishedMatchday(day_number=day, first_kickoff=min(dates), last_kickoff=max(dates))
        )
    return sorted(out, key=lambda m: m.day_number)


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
    picking one number. `day_number` is the schedule's matchday number for
    `at` -- only the schedule carries it, so it's None whenever `source` is
    `"myeleven"` or `"none"`.
    """

    at: datetime | None
    source: str
    cross_check: datetime | None
    matchday_in_progress: bool
    day_number: int | None = None
