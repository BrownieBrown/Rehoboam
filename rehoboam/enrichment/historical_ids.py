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
don't carry it at all), and neither live Kickbase endpoint used to backfill
history for a recovered id (``get_competition_player_performance`` /
``get_player_market_value_history_v2``) returns position either — both were
checked directly against the live API and neither response carries a
position field anywhere. So this module deliberately returns ids only;
``sweep.run_sweep``'s ``extra_player_ids`` handling upserts a position-less
stub row for each one it doesn't already know about.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def gather_historical_player_ids(db_path: Path) -> list[str]:
    """Union of every player id referenced by past trading activity.

    Returns a sorted list of distinct ids as strings. Safe to call against
    an empty (freshly created) learning DB — returns ``[]`` rather than
    raising when the tables are empty.
    """
    ids: set[str] = set()
    with sqlite3.connect(db_path) as conn:
        for (pid,) in conn.execute("SELECT DISTINCT player_id FROM flip_outcomes"):
            ids.add(str(pid))
        for (pid,) in conn.execute("SELECT DISTINCT player_id FROM player_mv_history"):
            ids.add(str(pid))
        for (blob,) in conn.execute("SELECT lineup_player_ids FROM matchday_lineup_results"):
            for pid in json.loads(blob):
                ids.add(str(pid))
    return sorted(ids)
