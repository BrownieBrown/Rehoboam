"""Recover player ids that left the league before the current sweep.

``fetch_universe`` (``sweep.py``) enumerates only *currently registered*
players via ``/v4/leagues/{lid}/lineup/selection`` — correct for the live
trading path, but a backtest replaying a past season needs every player who
was ever actually held, including anyone who has since transferred out of
the Bundesliga and so no longer shows up in that endpoint at all.

This module recovers those departed ids from the three tables in the
learning DB (``bid_learning.db``, written by ``BidLearner``) that still
reference them by id, even though the player is gone from the live league:

- ``flip_outcomes.player_id`` — every buy+sell we ever completed
- ``matchday_lineup_results.lineup_player_ids`` — a JSON array of the 11
  ids actually fielded, one row per matchday
- ``player_mv_history.player_id`` — every player whose market value we
  snapshotted while we held them

None of these three tables carry a reliable position for most rows (the
column exists on ``flip_outcomes`` but is populated for only a small
fraction of historical player ids in practice, and the other two tables
don't carry it at all), and neither of the two Kickbase endpoints used to
backfill history for a recovered id (``get_competition_player_performance`` /
``get_player_market_value_history_v2``) returns position either — both were
checked directly against the live API and neither response carries a
position field anywhere. So this module deliberately returns ids only, not
position: ``sweep.run_sweep``'s ``extra_player_ids`` handling resolves
position (and first/last name, team) for each one separately, via a third
endpoint (``get_competition_player_details``) that does carry it. An id only
keeps ``position IS NULL`` if that resolution genuinely fails or the
response omits a usable position code — the exception, not the norm.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def gather_historical_player_ids(db_path: Path) -> list[str]:
    """Union of every player id referenced by past trading activity.

    Returns a sorted list of distinct ids as strings. Safe to call against
    an empty (freshly created) learning DB — returns ``[]`` rather than
    raising when the tables are empty.

    ``lineup_player_ids`` is written by a single ``json.dumps`` call today,
    so a malformed row is unlikely — but CLAUDE.md's own documented
    debugging workflow is opening the SQLite file directly
    (``sqlite3 logs/bid_learning.db``), which makes a hand-edited row a real
    path, not a hypothetical. A row that fails to parse as a JSON list
    (invalid JSON text, or valid JSON that isn't a list) is skipped and
    logged rather than allowed to abort the whole sweep, matching every
    other per-item failure path this module's caller (``sweep.run_sweep``)
    already treats this way — this is the one that didn't, until now.
    """
    ids: set[str] = set()
    with sqlite3.connect(db_path) as conn:
        for (pid,) in conn.execute("SELECT DISTINCT player_id FROM flip_outcomes"):
            ids.add(str(pid))
        for (pid,) in conn.execute("SELECT DISTINCT player_id FROM player_mv_history"):
            ids.add(str(pid))
        for (blob,) in conn.execute("SELECT lineup_player_ids FROM matchday_lineup_results"):
            try:
                parsed = json.loads(blob)
                if not isinstance(parsed, list):
                    raise TypeError(f"expected a JSON list, got {type(parsed).__name__}")
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    "Skipping malformed matchday_lineup_results.lineup_player_ids row " "(%r): %s",
                    blob,
                    e,
                )
                continue
            for pid in parsed:
                ids.add(str(pid))
    return sorted(ids)
