"""One league-state refresh per ingest run (spec G1): ~35 requests before the per-player loop.

Every fetch is its own try/except: a manager whose squad call fails costs one
`failed` and nothing else. Requests are counted here as well so the caller can
add them to the run's totals (the client passed in is already budget-counting).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rehoboam.enrichment.budget import BudgetExhausted
from rehoboam.enrichment.rows import (
    fixture_rows,
    league_table_rows,
    manager_rows,
    manager_squad_rows,
    market_listing_rows,
    team_row,
)

logger = logging.getLogger(__name__)

TEAMS_STALE_AFTER_S = 7 * 86400


@dataclass
class LeagueRefreshStats:
    listings: int = 0
    managers: int = 0
    squads: int = 0
    transfers: int = 0
    fixtures: int = 0
    table_rows: int = 0
    teams: int = 0
    requests: int = 0
    failed: int = 0


def run_league_refresh(
    client,
    league_store,
    learner,
    *,
    league_id: str,
    our_user_id: str,
    season: str,
    now: float,
    teams_stale_after_s: float = TEAMS_STALE_AFTER_S,
) -> LeagueRefreshStats:
    stats = LeagueRefreshStats()

    def attempt(what, fn):
        try:
            result = fn()
        except BudgetExhausted:
            raise
        except Exception as e:  # noqa: BLE001
            stats.requests += 1
            stats.failed += 1
            logger.warning("league-refresh %s failed: %s", what, e)
            return None
        stats.requests += 1
        return result

    # Market: the client keeps the raw payload; the parsed list is not needed here.
    if attempt("market", lambda: client.get_market(league_id)) is not None:
        payload = getattr(client, "last_market_payload", None)
        if payload:
            stats.listings = league_store.write_listings(
                market_listing_rows(
                    payload, snapshot_at=now, our_user_id=our_user_id, source="ingest"
                )
            )
        else:
            logger.warning("league-refresh market: no payload on the client")

    ranking = attempt("ranking", lambda: client.get_league_ranking(league_id))
    managers = manager_rows(
        ranking or {}, league_id=league_id, our_user_id=our_user_id, updated_at=now
    )
    if managers:
        stats.managers = league_store.upsert_managers(managers)

    squad_rows: list[dict] = []
    transfer_rows: list[dict] = []
    for m in managers:
        mid = m["manager_id"]
        squad = attempt(f"squad {mid}", lambda mid=mid: client.get_manager_squad(league_id, mid))
        if squad is not None:
            squad_rows += manager_squad_rows(
                mid, (squad or {}).get("it") or [], snapshot_at=now, source="ingest"
            )
        history = attempt(
            f"transfers {mid}",
            lambda mid=mid: client.get_manager_transfer_history(league_id, mid),
        )
        for t in (history or {}).get("it") or []:
            pid, tdt = t.get("pi"), t.get("dt")
            if not pid or not tdt:
                continue
            transfer_rows.append(
                {
                    "league_id": league_id,
                    "manager_id": mid,
                    "transfer_dt": tdt,
                    "player_id": str(pid),
                    "player_name": t.get("pn", ""),
                    "transfer_type": t.get("tty"),
                    "transfer_price": t.get("trp"),
                }
            )
    if squad_rows:
        stats.squads = league_store.write_squads(squad_rows)
    if transfer_rows and learner is not None:
        stats.transfers = int(learner.record_manager_transfers(transfer_rows) or len(transfer_rows))

    schedule = attempt("schedule", lambda: client.get_competition_matchdays())
    if schedule:
        stats.fixtures = league_store.upsert_fixtures(
            fixture_rows(schedule, season=season, updated_at=now)
        )

    table = attempt("table", lambda: client.get_competition_table())
    if table:
        day_number = int((ranking or {}).get("day") or 0)
        stats.table_rows = league_store.write_table(
            league_table_rows(table, season=season, day_number=day_number, updated_at=now)
        )

    for tid in league_store.teams_older_than(now - teams_stale_after_s):
        profile = attempt(f"team {tid}", lambda tid=tid: client.get_team_profile(league_id, tid))
        row = team_row(profile or {}, updated_at=now)
        if row:
            stats.teams += league_store.upsert_teams([row])

    logger.info(
        "league-refresh listings=%d managers=%d squads=%d transfers=%d fixtures=%d table=%d "
        "teams=%d requests=%d failed=%d",
        stats.listings,
        stats.managers,
        stats.squads,
        stats.transfers,
        stats.fixtures,
        stats.table_rows,
        stats.teams,
        stats.requests,
        stats.failed,
    )
    return stats
