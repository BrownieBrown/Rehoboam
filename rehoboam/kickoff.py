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
from datetime import datetime, timedelta, timezone

#: A Bundesliga round spans at most Friday to Monday. A round whose first
#: kickoff is older than this is a PREVIOUS round, never one in progress --
#: otherwise one un-rolled or postponed fixture would unlock trading for good.
MATCHDAY_ROUND_SPAN_DAYS = 4


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


@dataclass(frozen=True)
class Round:
    """One matchday group of the schedule, reduced to what the guards need."""

    day_number: int | None
    first_kickoff: datetime
    started: bool  # first kickoff passed, or any fixture reports st != 0
    finished: bool  # every fixture reports st == 2


def rounds_from_matchdays(payload, now: datetime) -> list[Round]:
    """Every matchday group with at least one parseable date, schedule order.

    A group is *started* once its earliest fixture has kicked off or any
    fixture says so (`st != 0`); it is *finished* when every fixture says
    `st == 2`. Tolerant of missing keys and unparseable dates -- a schema
    drift here should degrade to "the schedule has nothing to say", not
    raise.
    """
    out: list[Round] = []
    for day, fixtures in _day_groups(payload):
        dates = [d for d in (_parse_match_date(f.get("dt")) for f in fixtures) if d is not None]
        if not dates:
            continue
        first = min(dates)
        out.append(
            Round(
                day_number=day,
                first_kickoff=first,
                started=first <= now or any(f.get("st") != 0 for f in fixtures),
                finished=all(f.get("st") == 2 for f in fixtures),
            )
        )
    return out


def next_fixture_from_matchdays(payload: dict, now: datetime) -> NextFixture | None:
    """First kickoff of the earliest round that has not started, with its day.

    Round-aware since 2026-10-12. Kickbase locks the fielded eleven and
    applies the negative-budget rule at the ROUND'S first kickoff (Friday
    evening); the Saturday and Sunday fixtures of a round already under way
    change nothing for it, and every squad change lands on the following
    round. So once a round has started, its remaining fixtures are not a
    "next kickoff" for any guard -- the next kickoff that matters is the
    first fixture of the next round. Before this, the earliest remaining
    fixture was reported, which read "0d to kickoff" all weekend and locked
    the bot out of Friday night, Saturday and Sunday (2026-10-10: three
    must-have buys refused by the budget lockout, two points buys sold by
    the debt recovery, for a kickoff that was really five days away).
    """
    best: NextFixture | None = None
    for rnd in rounds_from_matchdays(payload, now):
        if rnd.started:
            continue
        if best is None or rnd.first_kickoff < best.at:
            best = NextFixture(day_number=rnd.day_number, at=rnd.first_kickoff)
    return best


def next_kickoff_from_matchdays(payload: dict, now: datetime) -> datetime | None:
    """First kickoff of the next round that has not started (see above).

    `payload["it"]` is a list of matchday groups, each with its own `it` list
    of fixtures. Delegates to `next_fixture_from_matchdays` and drops the
    matchday number -- kept for callers that only ever needed the timestamp.
    """
    nf = next_fixture_from_matchdays(payload, now)
    return nf.at if nf else None


def matchday_in_progress_from_matchdays(
    payload, now: datetime, *, span_days: int = MATCHDAY_ROUND_SPAN_DAYS
) -> bool:
    """Is a round under way: started within `span_days` and not yet finished.

    The schedule's answer to REH-110's question, replacing the `/myeleven`
    reading that only saw OUR players' fixtures -- on 2026-10-09/10 none of
    ours played the Friday game, so the round "had not started" until our
    first player kicked off on Saturday afternoon. The span keeps a
    postponed fixture from holding a round open for weeks.
    """
    cutoff = now - timedelta(days=span_days)
    return any(
        cutoff <= rnd.first_kickoff <= now and not rnd.finished
        for rnd in rounds_from_matchdays(payload, now)
    )


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
