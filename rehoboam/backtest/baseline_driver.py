"""Driver for the season-average baseline measurement.

This is the composition that produced week 1's headline regret number,
promoted from a throwaway scratchpad script into a committed, reproducible
path: weeks 2-3 must compare their scorer against this exact same fixture
set (same squads, same matchdays, same actuals), so the wiring has to be a
callable, not a one-off.

Reads two local SQLite stores, both read-only here:

- ``bid_learning.db`` (via ``BidLearner``'s schema) — ``matchday_lineup_results``
  for the fielded eleven per matchday, ``flip_outcomes`` for hold windows.
- ``training_corpus.db`` (``TrainingCorpus``) — ``player_match_history`` for
  actual points, ``player_universe`` for position.

No network calls. See ``rehoboam.cli.backtest_baseline`` for the CLI entry
point and docs/superpowers/specs/2026-07-29-rehoboam-v2-design.md §6 for why
this number is reported as an upper bound, not a point estimate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rehoboam.backtest.baselines import season_average_baseline
from rehoboam.backtest.harness import BacktestReport, MatchdayInput, run_backtest
from rehoboam.backtest.squad_reconstruction import squad_on_matchday
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.kickbase_client import Player

DEFAULT_SEASON = "2025/2026"
DEFAULT_MAX_SQUAD_SIZE = 15

# A reconstructed matchday whose squad (after dropping ids with no resolved
# position) has fewer than this many players is dropped as unusable. Below
# this, a squad of exactly 11 forces `select_best_eleven` to field everyone
# regardless of score — regret is trivially zero, which would silently
# inflate the "captured" percentage rather than report an honest gap. This
# also means the surviving "usable" matchdays skew toward the *over*-counted
# side of squad reconstruction (see the spec's §6 selection-effect caveat) —
# a bias in the same direction as `--max-squad-size` corrects for.
MIN_USABLE_SQUAD_SIZE = 12


@dataclass
class BaselineDriverStats:
    matchdays_total: int
    matchdays_usable: int
    matchdays_skipped_small_squad: int


def _to_epoch(iso_str: str) -> float:
    """Convert ISO-8601 with optional 'Z' suffix to a unix epoch float."""
    if iso_str.endswith("Z"):
        iso_str = iso_str[:-1] + "+00:00"
    return datetime.fromisoformat(iso_str).timestamp()


def _load_lineup_rows(learner_db_path: Path) -> list[dict]:
    with sqlite3.connect(learner_db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT league_id, day_number, matchday_date, lineup_player_ids "
            "FROM matchday_lineup_results ORDER BY day_number"
        ).fetchall()
    return [dict(r) for r in rows]


def _load_flips(learner_db_path: Path) -> list[dict]:
    with sqlite3.connect(learner_db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT player_id, buy_date, sell_date FROM flip_outcomes").fetchall()
    return [dict(r) for r in rows]


def _cap_squad(
    squad_ids: set[str],
    fielded_ids: list[str],
    flips: list[dict],
    matchday_ts: float,
    max_squad_size: int | None,
) -> set[str]:
    """Cap a reconstructed squad at ``max_squad_size``, fielded players first.

    The fielded eleven (ten, on the three -100-penalty matchdays) is always
    kept. Any remaining capacity is filled with the rest of the squad
    ordered by most-recently-bought — the buy_date of whichever flip window
    covers this matchday — dropping the longest-held bench players first.

    Uncapped reconstruction can legitimately exceed the real 15-player
    squad limit, because hold-window unioning over-counts membership on
    some matchdays (see ``squad_reconstruction`` module docstring). That
    inflates ``total_best_points`` (more bodies can only raise the
    hindsight-optimal eleven) without ever letting ``total_chosen_points``
    rise to match, since a season-average ranker never picks a player it
    didn't already have visibility into. Capping removes that one-sided
    inflation; ``max_squad_size=None`` reproduces the uncapped historical
    figure.
    """
    if max_squad_size is None or len(squad_ids) <= max_squad_size:
        return squad_ids

    fielded_set = {str(pid) for pid in fielded_ids}
    fielded = [pid for pid in squad_ids if pid in fielded_set]
    remaining_capacity = max(max_squad_size - len(fielded), 0)
    candidates = squad_ids - set(fielded)

    def buy_date_for(pid: str) -> float:
        windows = [
            f["buy_date"]
            for f in flips
            if str(f["player_id"]) == pid and f["buy_date"] <= matchday_ts <= f["sell_date"]
        ]
        return max(windows) if windows else 0.0

    ranked = sorted(candidates, key=buy_date_for, reverse=True)
    return set(fielded) | set(ranked[:remaining_capacity])


def build_matchday_inputs(
    corpus: TrainingCorpus,
    *,
    learner_db_path: Path,
    season: str = DEFAULT_SEASON,
    max_squad_size: int | None = DEFAULT_MAX_SQUAD_SIZE,
) -> tuple[list[MatchdayInput], BaselineDriverStats]:
    """Reconstruct one ``MatchdayInput`` per usable matchday in ``season``.

    For each ``matchday_lineup_results`` row: reconstruct squad membership
    via ``squad_on_matchday`` (fielded eleven ∪ flip hold-windows active at
    kickoff), optionally cap it (``_cap_squad``), resolve position via
    ``corpus.positions_for`` (dropping ids with no resolved position — they
    cannot be placed into a formation), and look up each surviving member's
    actual points for that exact ``(season, day_number)`` from
    ``player_match_history``. Matchdays whose usable squad falls below
    ``MIN_USABLE_SQUAD_SIZE`` are dropped — see that constant's docstring.
    """
    lineup_rows = _load_lineup_rows(learner_db_path)
    flips = _load_flips(learner_db_path)

    matchdays: list[MatchdayInput] = []
    skipped = 0

    for row in lineup_rows:
        fielded_ids = json.loads(row["lineup_player_ids"])
        matchday_ts = _to_epoch(row["matchday_date"])
        squad_ids = squad_on_matchday(flips, fielded_ids, matchday_ts)
        squad_ids = _cap_squad(squad_ids, fielded_ids, flips, matchday_ts, max_squad_size)

        positions = corpus.positions_for(list(squad_ids))
        players = [
            Player(
                id=pid,
                first_name="",
                last_name=pid,
                position=positions[pid],
                team_id="",
                team_name="",
                market_value=0,
                points=0,
                average_points=0.0,
            )
            for pid in squad_ids
            if pid in positions
        ]

        if len(players) < MIN_USABLE_SQUAD_SIZE:
            skipped += 1
            continue

        actual_points: dict[str, float] = {}
        for pid in squad_ids:
            for match in corpus.matches_for_player(pid):
                if match["season"] == season and match["day_number"] == row["day_number"]:
                    actual_points[pid] = float(match["points"])
                    break

        matchdays.append(
            MatchdayInput(day_number=row["day_number"], squad=players, actual_points=actual_points)
        )

    stats = BaselineDriverStats(
        matchdays_total=len(lineup_rows),
        matchdays_usable=len(matchdays),
        matchdays_skipped_small_squad=skipped,
    )
    return matchdays, stats


def run_baseline(
    *,
    learner_db_path: Path,
    corpus_db_path: Path,
    season: str = DEFAULT_SEASON,
    max_squad_size: int | None = DEFAULT_MAX_SQUAD_SIZE,
) -> tuple[BacktestReport, BaselineDriverStats]:
    """Reconstruct fixtures and run ``season_average_baseline`` through the harness.

    This is the whole composition described in the PR: read the learning DB,
    reconstruct squads, join positions, score with the season-average model,
    and hand back the report weeks 2-3 must beat on this identical fixture
    set.
    """
    corpus = TrainingCorpus(db_path=corpus_db_path)
    matchdays, stats = build_matchday_inputs(
        corpus,
        learner_db_path=learner_db_path,
        season=season,
        max_squad_size=max_squad_size,
    )
    report = run_backtest(
        corpus,
        lambda _player_id, history: season_average_baseline(history),
        season=season,
        matchdays=matchdays,
    )
    return report, stats
