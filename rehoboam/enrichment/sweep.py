"""League-wide corpus sweep — the long pole of week 1.

Enumerates every selectable player in the league and pulls per-match
performance plus the full market-value series into ``TrainingCorpus``. Around
a thousand requests, so it is throttled, resumable, and tolerant of individual
failures.

Resumability is the important property: ``sweep_progress`` is only marked
after a successful write, so an interrupted or partially-failed run picks up
exactly where it stopped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from rehoboam.enrichment.corpus import TrainingCorpus

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAME_DAYS = 365
DEFAULT_THROTTLE_SECONDS = 0.25
DEFAULT_PAGE_SIZE = 50
# Generous ceiling: the largest real position (MID) needed 4 pages. This only
# exists so a misbehaving endpoint cannot spin the loop forever.
DEFAULT_MAX_PAGES_PER_POSITION = 40

_POSITIONS = {1: "Goalkeeper", 2: "Defender", 3: "Midfielder", 4: "Forward"}


@dataclass
class SweepStats:
    universe_size: int = 0
    performance_fetched: int = 0
    mv_fetched: int = 0
    transfers_fetched: int = 0
    failed: int = 0
    skipped: int = 0
    positions_resolved: int = 0
    positions_unresolved: int = 0


def _universe_to_rows(items: list[dict]) -> list[dict]:
    """Map lineup-selection items to ``upsert_players`` rows.

    Field names are measured from the live endpoint, not inherited from
    ``Player.from_dict`` — the two disagree. Here the id is ``pi``, and there
    is no first-name field at all, so ``first_name`` is always None.
    """
    rows = []
    for item in items:
        pid = item.get("pi")
        if pid is None:
            continue
        rows.append(
            {
                "player_id": str(pid),
                "first_name": None,
                "last_name": item.get("n"),
                "position": _POSITIONS.get(item.get("pos"), None),
                "team_id": item.get("tid"),
                "market_value": item.get("mv"),
                "average_points": item.get("ap"),
            }
        )
    return rows


def fetch_universe(
    client,
    league_id: str,
    *,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages_per_position: int = DEFAULT_MAX_PAGES_PER_POSITION,
) -> list[dict]:
    """Enumerate every selectable player by sweeping positions 1-4.

    ``position`` is mandatory on the endpoint — omitting it returns zero items
    rather than an error — so the universe is the union of four per-position
    sweeps, deduplicated by player id.

    Pagination note, load-bearing: the server caps each page at 50 items no
    matter what ``page_size`` requests. ``start`` therefore advances by the
    number of items actually returned. Advancing by ``page_size`` would skip
    every other page while still looking like a successful sweep.
    """
    by_id: dict[str, dict] = {}

    for position in (1, 2, 3, 4):
        start = 0
        for _ in range(max_pages_per_position):
            payload = client.get_lineup_selection(
                league_id=league_id,
                position=position,
                start=start,
                max_items=page_size,
            )
            items = payload.get("it") or []
            if not items:
                break

            for row in _universe_to_rows(items):
                by_id[row["player_id"]] = row

            start += len(items)  # actual count, never page_size
            if throttle_seconds:
                time.sleep(throttle_seconds)
        else:
            logger.warning(
                "Position %d hit the %d-page ceiling — universe may be truncated",
                position,
                max_pages_per_position,
            )

    logger.info("Universe: %d distinct players across 4 positions", len(by_id))
    return list(by_id.values())


def run_sweep(
    client,
    corpus: TrainingCorpus,
    *,
    league_id: str,
    dry_run: bool = False,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    limit: int | None = None,
    timeframe_days: int = DEFAULT_TIMEFRAME_DAYS,
    extra_player_ids: list[str] | None = None,
    force_refetch_performance: bool = False,
    sweep_transfers: bool = False,
) -> SweepStats:
    """Populate the training corpus for every player in the league.

    ``dry_run`` fetches the universe (so the size estimate is real) but
    performs no per-player fetches and no history writes.

    ``extra_player_ids`` recovers players who are no longer in the live
    universe at all — ``fetch_universe`` only sees players currently
    registered via ``/lineup/selection``, so anyone who transferred out of
    the Bundesliga since is invisible to it, even though a backtest
    replaying a past season needs their history too. Each id not already
    covered by the live sweep first gets a bare ``player_universe`` stub (id
    only) via ``TrainingCorpus.ensure_players`` — an insert, never an
    overwrite, so a rerun can't clobber a row that has since gained real
    data some other way — and is then resolved via
    ``get_competition_player_details``, the only one of the three endpoints
    this sweep uses that carries a position (``get_competition_player_performance``
    and ``get_player_market_value_history_v2`` do not — checked directly
    against the live API). A player whose lookup fails or omits ``pos``
    keeps ``position IS NULL`` rather than a guessed value, and is retried
    on the next run (``TrainingCorpus.players_missing_position`` drives
    that: an id only drops off once it has a real position). See
    ``rehoboam.enrichment.historical_ids`` for where the ids come from.

    ``force_refetch_performance`` clears every player's
    ``performance_fetched_at`` (via ``TrainingCorpus.clear_performance_fetched``)
    before computing the pending-performance list, so a rerun re-fetches
    performance for players ``sweep_progress`` already marks complete —
    needed after a bug fix in how performance rows are parsed, since a plain
    rerun would otherwise skip everyone. Scoped to performance only: MV-series
    resumability (``mv_fetched_at``) is untouched, and this never fires
    silently — it is opt-in per call, never the default.

    ``sweep_transfers`` (REH-55) additionally fetches each pending player's
    real transfer history via ``get_player_transfer_history`` and persists it
    through ``TrainingCorpus.record_player_transfers``, tracked by its own
    ``sweep_progress.transfers_fetched_at`` column — so it is independently
    resumable from performance/MV, and a rerun with the flag on only retries
    players that don't have it yet. Off by default: it adds one request per
    player (~527 in the live universe) on top of the existing sweep, and a
    plain ``enrich-corpus`` run should not pay that cost unless asked.
    """
    stats = SweepStats()

    rows = fetch_universe(client, league_id, throttle_seconds=throttle_seconds)
    stats.universe_size = len(rows)
    corpus.upsert_players(rows)

    extra_ids: list[str] = []
    if extra_player_ids:
        live_ids = {r["player_id"] for r in rows}
        extra_ids = sorted({str(pid) for pid in extra_player_ids if str(pid) not in live_ids})
        if extra_ids:
            added = corpus.ensure_players(extra_ids)
            stats.universe_size += len(extra_ids)
            logger.info(
                "Historical ids: %d supplied outside the live universe, %d new stub rows (%d already tracked)",
                len(extra_ids),
                added,
                len(extra_ids) - added,
            )

    if dry_run:
        return stats

    if extra_ids:
        for pid in corpus.players_missing_position(extra_ids):
            try:
                details = client.get_competition_player_details(player_id=pid)
            except Exception as e:
                stats.positions_unresolved += 1
                logger.warning("Position lookup failed for historical player %s: %s", pid, e)
                if throttle_seconds:
                    time.sleep(throttle_seconds)
                continue

            position = _POSITIONS.get(details.get("pos"))
            if position is None:
                stats.positions_unresolved += 1
                logger.warning("Competition player details for %s carried no usable position", pid)
            else:
                corpus.upsert_players(
                    [
                        {
                            "player_id": pid,
                            "first_name": details.get("fn"),
                            "last_name": details.get("ln"),
                            "position": position,
                            "team_id": details.get("tid"),
                            "market_value": details.get("mv"),
                            "average_points": details.get("ap"),
                        }
                    ]
                )
                stats.positions_resolved += 1
            if throttle_seconds:
                time.sleep(throttle_seconds)

        logger.info(
            "Historical positions: %d resolved, %d unresolved",
            stats.positions_resolved,
            stats.positions_unresolved,
        )

    team_by_id = {r["player_id"]: r.get("team_id") for r in rows}

    if force_refetch_performance:
        cleared = corpus.clear_performance_fetched()
        logger.info("Forced re-fetch: cleared performance_fetched_at for %d players", cleared)

    pending_perf = corpus.players_needing_fetch("performance")
    pending_mv = corpus.players_needing_fetch("mv")
    stats.skipped = stats.universe_size - len(pending_perf)

    if limit is not None:
        pending_perf = pending_perf[:limit]
        pending_mv = pending_mv[:limit]

    for pid in pending_perf:
        try:
            perf = client.get_competition_player_performance(player_id=pid)
            corpus.record_match_history(pid, team_by_id.get(pid), perf)
            corpus.mark_fetched(pid, performance=True)
            stats.performance_fetched += 1
        except Exception as e:
            stats.failed += 1
            logger.warning("Performance fetch failed for player %s: %s", pid, e)
        if throttle_seconds:
            time.sleep(throttle_seconds)

    for pid in pending_mv:
        try:
            history = client.get_player_market_value_history_v2(
                player_id=pid, timeframe=timeframe_days
            )
            corpus.record_mv_series(pid, history)
            corpus.mark_fetched(pid, mv=True)
            stats.mv_fetched += 1
        except Exception as e:
            stats.failed += 1
            logger.warning("MV fetch failed for player %s: %s", pid, e)
        if throttle_seconds:
            time.sleep(throttle_seconds)

    if sweep_transfers:
        pending_transfers = corpus.players_needing_fetch("transfers")
        if limit is not None:
            pending_transfers = pending_transfers[:limit]

        for pid in pending_transfers:
            try:
                history = client.get_player_transfer_history(league_id=league_id, player_id=pid)
                corpus.record_player_transfers(pid, history)
                corpus.mark_fetched(pid, transfers=True)
                stats.transfers_fetched += 1
            except Exception as e:
                stats.failed += 1
                logger.warning("Transfer history fetch failed for player %s: %s", pid, e)
            if throttle_seconds:
                time.sleep(throttle_seconds)

    logger.info(
        "Sweep done: %d perf, %d mv, %d transfers, %d failed, %d skipped",
        stats.performance_fetched,
        stats.mv_fetched,
        stats.transfers_fetched,
        stats.failed,
        stats.skipped,
    )
    return stats
