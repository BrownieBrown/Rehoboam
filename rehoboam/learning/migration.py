"""One-time migration of legacy JSON state files into bid_learning.db.

Until rehoboam ran on Azure, two JSON files in `logs/` held operational
state — `pending_bids.json` (active auctions) and `tracked_purchases.json`
(currently held players + cost basis). Azure's deployment only synced
SQLite databases between runs, so these files were silently wiped each
invocation.

This module imports any leftover JSON content into the new
`pending_bids` and `tracked_purchases` tables on first boot, then
renames the source files to `.bak` so future boots skip the work.
The DB rows are authoritative — if a row already exists for a given
player_id, the JSON entry is discarded rather than overwriting it.
"""

import json
import logging
from pathlib import Path
from typing import Any

from ..bid_learner import BidLearner

logger = logging.getLogger(__name__)


def _load_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not read %s for migration: %s", path, e)
        return None


def _archive(path: Path) -> None:
    """Rename `foo.json` to `foo.json.bak` so we don't re-import next boot.

    `Path.with_suffix` would replace the existing `.json` rather than
    appending, so we build the target name explicitly.
    """
    bak = path.with_name(path.name + ".bak")
    try:
        path.rename(bak)
    except Exception as e:
        logger.warning("Could not archive %s after migration: %s", path, e)


def _migrate_pending_bids(learner: BidLearner, path: Path) -> int:
    data = _load_json(path)
    if data is None:
        return 0

    existing_ids = {b["player_id"] for b in learner.get_pending_bids()}
    imported = 0
    failed = 0
    for bid in data:
        try:
            player_id = bid["player_id"]
            if player_id in existing_ids:
                continue
            learner.add_pending_bid(
                player_id=player_id,
                player_name=bid["player_name"],
                our_bid=bid["our_bid"],
                asking_price=bid["asking_price"],
                our_overbid_pct=bid["our_overbid_pct"],
                timestamp=bid["timestamp"],
                market_value=bid.get("market_value"),
                player_value_score=bid.get("player_value_score"),
                sell_plan_player_ids=bid.get("sell_plan_player_ids"),
            )
            imported += 1
        except (KeyError, TypeError) as e:
            failed += 1
            logger.warning("Skipping malformed pending_bid entry %r: %s", bid, e)

    # If every entry failed (corrupted/mid-write JSON) keep the file on
    # disk for human inspection — silently archiving it would permanently
    # discard state the operator believes was migrated. A successful
    # import (or a clean dedup against existing DB rows) still archives.
    if imported > 0 or failed == 0:
        _archive(path)
    return imported


def _migrate_tracked_purchases(learner: BidLearner, path: Path) -> int:
    data = _load_json(path)
    if data is None:
        return 0

    imported = 0
    failed = 0
    for player_id, info in data.items():
        try:
            if learner.get_tracked_purchase(player_id) is not None:
                continue
            learner.add_tracked_purchase(
                player_id=player_id,
                player_name=info["player_name"],
                buy_price=info["buy_price"],
                buy_date=info["buy_date"],
                source=info.get("source"),
            )
            imported += 1
        except (KeyError, TypeError) as e:
            failed += 1
            logger.warning("Skipping malformed tracked_purchase %s=%r: %s", player_id, info, e)

    if imported > 0 or failed == 0:
        _archive(path)
    return imported


def migrate_json_state_if_needed(
    learner: BidLearner,
    *,
    pending_bids_path: Path,
    tracked_purchases_path: Path,
) -> dict[str, int]:
    """Import legacy JSON state into BidLearner tables (idempotent).

    Returns a dict of how many rows were imported per file, for logging.
    Files that don't exist (already migrated, or fresh deploy) are
    silently skipped. DB rows always win over JSON entries with the
    same player_id — Azure-restored state is never clobbered by stale
    local JSON.
    """
    return {
        "pending_bids": _migrate_pending_bids(learner, pending_bids_path),
        "tracked_purchases": _migrate_tracked_purchases(learner, tracked_purchases_path),
    }
