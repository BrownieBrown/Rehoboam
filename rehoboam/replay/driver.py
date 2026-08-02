"""Composition root for the full-bot season replay.

Wires the corpus, the fitted v2 scorer, the reconstructed market and the real
standings into one run. This is the only place that knows about file paths and
the verified starting state, and the only place the leak boundary is applied.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from rehoboam.backtest.snapshot import matches_before
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.attribution import LeagueStanding, format_report
from rehoboam.replay.engine import (
    Matchday,
    SeasonResult,
    run_season,
    shipped_min_ep_gain,
)
from rehoboam.replay.market import ReplayMarket
from rehoboam.replay.state import initial_state
from rehoboam.scoring.v2.coefficients import load_coefficients

SEASON = "2025/2026"
LEAGUE_ID = "1933872"
MANAGER_ID = "3616202"
ASSIGNED_ON = 1754661947.0  # 2025-08-08T14:05:47Z, verified from /v4/leagues/{id}/overview
STARTING_BUDGET = 80_000_000

PLAYED_STATUSES = (1, 3, 4, 5)


def _parse(dt: str) -> float:
    return datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def load_calendar(learning_db_path: Path, *, league_id: str) -> dict[int, float]:
    """Authoritative kickoff per matchday, as unix epochs.

    ``player_match_history`` cannot supply this: it holds 36 distinct team ids
    for 2025/2026 against a league of 18, so two competitions with different
    calendars share one ``day_number`` space and day 1 spans 2025-08-01 to
    2025-08-24. ``matchday_lineup_results`` is our league's own fixture list —
    exactly one row per matchday, with the date the matchday actually started.
    """
    with sqlite3.connect(learning_db_path) as conn:
        rows = conn.execute(
            "SELECT day_number, matchday_date FROM matchday_lineup_results "
            "WHERE league_id = ? AND matchday_date IS NOT NULL",
            (league_id,),
        ).fetchall()
    return {int(day): _parse(str(date)) for day, date in rows}


def day_for_kickoff(kickoffs: dict[int, float], at: float) -> int:
    """The matchday being predicted at ``at``: the next one still to kick off.

    Resolving this from match dates instead (``MIN(day_number)`` over unplayed
    fixtures) lags badly whenever a low-numbered fixture is postponed — it put
    matchdays 21 through 24 all on a cutoff of 17, starving the scorer of four
    matchdays of history. A per-league calendar has no such overlap.
    """
    upcoming = [day for day, kickoff in kickoffs.items() if kickoff > at]
    if upcoming:
        return min(upcoming)
    # Past the final whistle of the season: every matchday is history.
    return max(kickoffs, default=0) + 1


def build_matchdays(
    corpus: TrainingCorpus, *, season: str, kickoffs: dict[int, float] | None = None
) -> list[Matchday]:
    """One ``Matchday`` per day_number, with real points and its kickoff.

    Points come from ``player_match_history`` keyed by ``day_number``; those
    reconcile exactly with the official league totals, so only the dates were
    ever wrong. Pass ``kickoffs`` (see ``load_calendar``) to date the matchdays
    from our league's own fixture list. Without it the kickoff falls back to
    the earliest match recorded under that day_number, which is only correct
    for a corpus holding a single competition.
    """
    with sqlite3.connect(corpus.db_path) as conn:
        rows = conn.execute(
            "SELECT day_number, player_id, points, match_date FROM player_match_history "
            "WHERE season = ? AND match_date IS NOT NULL ORDER BY day_number",
            (season,),
        ).fetchall()

    by_day: dict[int, dict[str, float]] = {}
    earliest: dict[int, float] = {}
    for day, pid, points, match_date in rows:
        day = int(day)
        by_day.setdefault(day, {})[str(pid)] = float(points or 0)
        at = _parse(match_date)
        if day not in earliest or at < earliest[day]:
            earliest[day] = at

    return [
        Matchday(
            day_number=day,
            kickoff=(kickoffs or {}).get(day, earliest[day]),
            points=by_day[day],
        )
        for day in sorted(by_day)
    ]


def load_standings(
    learning_db_path: Path, *, league_id: str, exclude_manager_id: str
) -> list[LeagueStanding]:
    """Final season totals for every manager except ours."""
    with sqlite3.connect(learning_db_path) as conn:
        rows = conn.execute(
            "SELECT manager_id, MAX(day_number), total_points FROM league_rank_history "
            "WHERE league_id = ? AND manager_id != ? GROUP BY manager_id",
            (league_id, exclude_manager_id),
        ).fetchall()
    return [
        LeagueStanding(manager_id=str(r[0]), name=str(r[0]), total_points=int(r[2] or 0))
        for r in rows
    ]


def _make_score_fn(
    corpus: TrainingCorpus, season: str, kickoffs: dict[int, float]
) -> Callable[[str, float], float]:
    """Score a player using only matches before the current matchday.

    The leak boundary lives here: ``matches_before`` truncates history to
    strictly earlier matchdays, and the cutoff is derived from the decision
    timestamp via the league calendar, never from the matchday being predicted.
    """
    availability, rate, _meta = load_coefficients()
    positions: dict[str, str] = {}

    def score(player_id: str, at: float) -> float:
        day = day_for_kickoff(kickoffs, at)
        history = matches_before(
            corpus.matches_for_player(player_id), season=season, day_number=day
        )
        prev_status = None
        for match in reversed(history):
            if match.get("status") in PLAYED_STATUSES:
                prev_status = int(match["status"])
                break
        if player_id not in positions:
            positions.update(corpus.positions_for([player_id]))
        position = positions.get(player_id)
        probs = availability.predict(prev_status)
        return sum(probs[s] * rate.predict(player_id, s, position) for s in PLAYED_STATUSES)

    return score


def run_replay(
    *,
    corpus_path: Path,
    learning_db_path: Path,
    min_ep_gain: float | None = None,
) -> tuple[SeasonResult, str]:
    """Replay the whole season and return the result plus a formatted report.

    ``min_ep_gain`` defaults to whatever the live bot ships with, so the replay
    describes the bot on main rather than an agent nobody deployed. Pass it
    explicitly only to answer a "what if the floor were X" question — and label
    any such run as a sensitivity check, not a counterfactual result.
    """
    corpus = TrainingCorpus(corpus_path)
    kickoffs = load_calendar(learning_db_path, league_id=LEAGUE_ID)
    matchdays = build_matchdays(corpus, season=SEASON, kickoffs=kickoffs)
    state = initial_state(
        corpus,
        manager_id=MANAGER_ID,
        assigned_on=ASSIGNED_ON,
        starting_budget=STARTING_BUDGET,
    )
    market = ReplayMarket(corpus)
    score_fn = _make_score_fn(corpus, SEASON, kickoffs)
    positions_cache: dict[str, str] = {}
    teams_cache: dict[str, str] = {}

    def position_fn(pid: str) -> str | None:
        if pid not in positions_cache:
            positions_cache.update(corpus.positions_for([pid]))
        return positions_cache.get(pid)

    def team_fn(pid: str) -> str | None:
        if pid not in teams_cache:
            teams_cache.update(corpus.team_ids_for([pid]))
        return teams_cache.get(pid)

    gain_floor = shipped_min_ep_gain() if min_ep_gain is None else min_ep_gain

    result = run_season(
        state=state,
        market=market,
        matchdays=matchdays,
        score_fn=score_fn,
        mv_fn=corpus.market_value_at,
        position_fn=position_fn,
        team_fn=team_fn,
        min_ep_gain=gain_floor,
    )

    with sqlite3.connect(learning_db_path) as conn:
        actual_rows = conn.execute(
            "SELECT day_number, MAX(total_points), MAX(matchday_points) "
            "FROM league_rank_history WHERE is_self = 1 GROUP BY day_number",
            (),
        ).fetchall()
    actual_per_matchday = {int(r[0]): int(r[2] or 0) for r in actual_rows}
    actual_total = max((int(r[1] or 0) for r in actual_rows), default=0)

    standings = load_standings(learning_db_path, league_id=LEAGUE_ID, exclude_manager_id=MANAGER_ID)
    report = format_report(
        result,
        actual_total=actual_total,
        actual_per_matchday=actual_per_matchday,
        standings=standings,
        min_ep_gain=gain_floor,
    )
    return result, report
