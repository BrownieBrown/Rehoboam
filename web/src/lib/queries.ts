import "server-only";
import { cache } from "react";
import { sql } from "./db";
import { PAGE_SIZE } from "./paging";

// Tasks 5-8 append their own query functions to this file; this task owns
// only the two shell queries every page needs regardless of which page it is.

export type ShellFacts = {
  players: number | null;
  lastSessionAt: number | null;
  nextKickoff: number | null;
  formation: string | null;
  budget: number | null;
  lineupResult: string | null;
  stale: boolean;
};

/**
 * The sidebar and header need these on every data page, so this never throws:
 * when the store does not answer, the shell shows every fact as unknown
 * instead of taking the page down with it.
 *
 * `cache()` (not `fetch` memoization — `sql` is a raw Postgres client, so
 * Next's automatic per-request dedupe never applies to it) means the
 * layout's call and StatusHeader's call, within one render, share a single
 * execution: two round trips instead of four, and the sidebar's kickoff
 * card and the header's count line can no longer read two different
 * moments of the store.
 */
export const shellFacts = cache(async (): Promise<ShellFacts> => {
  const empty: ShellFacts = {
    players: null,
    lastSessionAt: null,
    nextKickoff: null,
    formation: null,
    budget: null,
    lineupResult: null,
    stale: true,
  };
  try {
    const [session] = await sql<
      {
        started_at: number;
        next_kickoff: number | null;
        legal_formation: string | null;
        budget: number | null;
        lineup_result: string | null;
      }[]
    >`
      select started_at, next_kickoff, legal_formation, budget, lineup_result
      from rehoboam.web_session_summary
      where app = 'function' and dry_run = 0
      order by started_at desc
      limit 1
    `;
    const [{ count }] = await sql<{ count: number }[]>`
      select count(*)::int as count from rehoboam.web_players
    `;
    if (!session) return { ...empty, players: count };
    return {
      players: count,
      lastSessionAt: session.started_at,
      nextKickoff: session.next_kickoff,
      formation: session.legal_formation,
      budget: session.budget,
      lineupResult: session.lineup_result,
      stale: Date.now() / 1000 - session.started_at > 14 * 3600,
    };
  } catch (error) {
    console.error("shellFacts failed", error);
    return empty;
  }
});

export type PlayerRow = {
  player_id: string;
  name: string;
  team: string | null;
  team_id: string | null;
  position: string;
  market_value: number | null;
  trend_24h_pct: number | null;
  trend_7d_pct: number | null;
  points: number | null;
  avg_points: number | null;
  median_points: number | null;
  points_per_million: number | null;
  appearances: number | null;
  starts: number | null;
  owner: string;
  predicted_ep: number | null;
  p_start: number | null;
  fair_value_gap: number | null;
  listed: boolean;
  /** Forecast for the next market-value update: euros, and percent with two decimals. */
  next_mv_change: number | null;
  next_mv_pct: number | null;
  /** What his average points are worth at his position's going rate, in euros. */
  fair_price: number | null;
  /** Every row the filters match, counted in the same statement as this page. */
  total: number;
};

export const PLAYER_SORTS = [
  "name",
  "position",
  "market_value",
  "trend_24h_pct",
  "trend_7d_pct",
  "next_mv_pct",
  "fair_price",
  "points",
  "avg_points",
  "median_points",
  "points_per_million",
  "appearances",
  "starts",
  "owner",
  "predicted_ep",
  "p_start",
  "fair_value_gap",
];

/** Our own manager name, for the "my squad" filter and the amber owner pill. */
export async function selfName(): Promise<string | null> {
  const [row] = await sql<{ manager: string }[]>`
    select manager from rehoboam.web_ownership where is_self limit 1
  `;
  return row?.manager ?? null;
}

export async function clubs(): Promise<string[]> {
  const rows = await sql<{ team: string }[]>`
    select distinct team from rehoboam.web_players where team is not null order by team
  `;
  return rows.map((r) => r.team);
}

export type PlayerFilter = {
  position?: string;
  owner?: "mine" | "free";
  club?: string;
  q?: string;
};

/** "mine" needs our manager name; when there is none, the filter matches nothing. */
async function ownerFilter(owner: PlayerFilter["owner"]): Promise<string | null> {
  return owner === "mine" ? ((await selfName()) ?? "") : null;
}

/**
 * The one WHERE clause the table and its count share. Synchronous on purpose:
 * a postgres.js fragment is a thenable, and returning one from an async
 * function would run it as a query of its own.
 */
function playerWhere(f: PlayerFilter, mine: string | null) {
  const like = f.q ? `%${f.q}%` : null;
  // Same truthy guard as `like` above: the club filter is driven by a plain
  // GET <select>, and its "All clubs" option submits `club=` (empty string,
  // not absent) - `?? null` alone would turn that into `team = ''`, which
  // matches nothing, silently breaking the reset-to-all-clubs path.
  const club = f.club ? f.club : null;
  // A free agent is a player no manager's newest squad snapshot holds.
  // `player_table.owner` says 'market' for one that is in the newest listing
  // snapshot and 'Kickbase' for one that is not; both are free agents.
  const free = f.owner === "free";
  return sql`
    where (${f.position ?? null}::text is null or position = ${f.position ?? null})
      and (${club}::text is null or team = ${club})
      and (${like}::text is null or name ilike ${like} or team ilike ${like})
      and (${mine}::text is null or owner = ${mine})
      and (${free} = false or owner in ('Kickbase', 'market'))
  `;
}

/** How many players the filters match. Used to hold a page number past the end on the last page. */
export async function playerCount(f: PlayerFilter): Promise<number> {
  const mine = await ownerFilter(f.owner);
  const [{ count }] = await sql<{ count: number }[]>`
    select count(*)::int as count from rehoboam.web_players ${playerWhere(f, mine)}
  `;
  return count;
}

/**
 * One page of the filtered table. `offset` comes from `pageOffset()` and is a
 * bound parameter. Each row carries `total`, the filtered count from this same
 * statement (the window runs before LIMIT/OFFSET), so "of N" always counts
 * what these pages list.
 */
export async function players(
  opts: PlayerFilter & { sort: string; dir: "asc" | "desc"; offset: number },
): Promise<PlayerRow[]> {
  const mine = await ownerFilter(opts.owner);
  // sql.unsafe(opts.sort) is safe ONLY because every caller runs opts.sort
  // through sortKey() against its own allow-list first (see sort.ts) - a
  // sort column is an identifier, not a value, so it can never be a bound
  // parameter. Never pass a raw search param to it.
  return sql<PlayerRow[]>`
    select *, count(*) over ()::int as total
    from rehoboam.web_players
    ${playerWhere(opts, mine)}
    order by ${sql.unsafe(opts.sort)} ${opts.dir === "asc" ? sql`asc` : sql`desc`} nulls last,
             player_id asc
    limit ${PAGE_SIZE} offset ${opts.offset}
  `;
}

export type SquadRow = {
  session_id: string;
  legal_formation: string | null;
  budget: number | null;
  sellable_value: number | null;
  next_kickoff: number | null;
  session_started_at: number;
  cost_basis_missing: number | null;
  player_id: string | null;
  name: string | null;
  team: string | null;
  position: string | null;
  market_value: number | null;
  points: number | null;
  avg_points: number | null;
  owner: string | null;
  predicted_ep: number | null;
  live_ep: number | null;
  in_best_11: boolean;
  p_start: number | null;
  cost_basis: number | null;
  gain_loss: number | null;
};

export async function squad(): Promise<SquadRow[]> {
  return sql<SquadRow[]>`
    select * from rehoboam.web_squad
    order by in_best_11 desc, predicted_ep desc nulls last, player_id asc
  `;
}

/**
 * `web_squad` left-joins the predictions, so a session that ran but recorded no
 * roster comes back as ONE row whose `player_id` is null - the session facts
 * survive, the players do not. Split the two before rendering.
 */
export function splitSquad(rows: SquadRow[]) {
  const players = rows.filter((r) => r.player_id !== null);
  return { session: rows[0] ?? null, players };
}

/** The integrity rules the newest real session raised, as rule + detail pairs. */
export async function latestSessionRules(): Promise<{ rule: string; detail: string }[]> {
  const [row] = await sql<{ integrity_details: Record<string, string> }[]>`
    select integrity_details from rehoboam.web_session_summary
    where app = 'function' and dry_run = 0
    order by started_at desc
    limit 1
  `;
  return Object.entries(row?.integrity_details ?? {}).map(([rule, detail]) => ({ rule, detail }));
}

export type MarketRow = {
  snapshot_at: number;
  player_id: string;
  name: string | null;
  team: string | null;
  position: string | null;
  ask: number;
  market_value: number | null;
  mv_trend: number | null;
  /** Always 0 in every stored listing: Kickbase does not share the real count. Not shown. */
  offer_count: number | null;
  our_bid: number | null;
  listed_at: number | null;
  expires_at: number | null;
  status: number | null;
  lineup_probability: number | null;
  seller: string;
  is_ours: boolean;
  predicted_ep: number | null;
  p_start: number | null;
  fair_value_gap: number | null;
  points: number | null;
  avg_points: number | null;
  /** Forecast for the next market-value update: euros, and percent with two decimals. */
  next_mv_change: number | null;
  next_mv_pct: number | null;
  /** What his average points are worth at his position's going rate, in euros. */
  fair_price: number | null;
};

export const MARKET_SORTS = [
  "name",
  "position",
  "ask",
  "market_value",
  "next_mv_pct",
  "fair_price",
  "seller",
  "expires_at",
  "predicted_ep",
  "p_start",
  "fair_value_gap",
];

/**
 * Every listing in the newest snapshot, sorted. Unfiltered on purpose: the
 * page narrows it with `expiringWithin`, so the snapshot time and the total
 * come from the same rows even when the filter matches nothing.
 */
export async function market(opts: { sort: string; dir: "asc" | "desc" }): Promise<MarketRow[]> {
  return sql<MarketRow[]>`
    select * from rehoboam.web_market
    order by ${sql.unsafe(opts.sort)} ${opts.dir === "asc" ? sql`asc` : sql`desc`} nulls last,
             player_id asc
  `;
}

export type ManagerRow = {
  manager_id: string;
  manager: string;
  is_self: boolean;
  squad_size: number;
  team_value: number | null;
  top: string[];
};

/**
 * One row per manager, from each manager's own newest squad snapshot. `top`
 * holds at most three names and only players that have a predicted score, so
 * it is empty rather than padded when a manager's players have none.
 */
export async function managers(): Promise<ManagerRow[]> {
  return sql<ManagerRow[]>`
    with ranked as (
      select manager_id, manager, is_self, player_name, market_value, predicted_ep,
             row_number() over (
               partition by manager_id order by predicted_ep desc nulls last, player_id
             ) as rank
      from rehoboam.web_ownership
    )
    select manager_id, manager, is_self,
        count(*)::int as squad_size,
        sum(market_value)::bigint as team_value,
        array_remove(array_agg(case when rank <= 3 and predicted_ep is not null
                                    then player_name end
                               order by rank), null) as top
    from ranked
    group by manager_id, manager, is_self
    order by is_self desc, team_value desc nulls last
  `;
}

export type CalibrationRow = {
  season: string;
  day_number: number;
  backfill: boolean;
  computed_at: number;
  n: number;
  n_unpredicted: number;
  n_stale_rows: number;
  mae: number | null;
  bias: number | null;
  spearman: number | null;
  baseline_spearman: number | null;
  spearman_played: number | null;
  top11_regret: number | null;
  baseline_top11_regret: number | null;
  squad_regret: number | null;
  live_spearman: number | null;
  live_n: number;
  worst: { player_id: string; position: string; predicted: number; actual: number }[];
  gate: Record<string, unknown> | null;
};

/** Every calibration report, live and backfill. The caller groups by matchday itself. */
export async function calibration(): Promise<CalibrationRow[]> {
  return sql<CalibrationRow[]>`
    select * from rehoboam.web_calibration
    order by day_number asc, backfill asc
  `;
}

export type MvAccuracyRow = {
  /** YYYY-MM-DD, the Berlin date of the update. */
  target_day: string;
  scored: number;
  unscorable: number;
  directional: number;
  direction_hits: number;
  mae_pct: number | null;
  baseline_mae_pct: number | null;
  mae_eur: number | null;
  baseline_mae_eur: number | null;
};

/** The newest scored updates first. The date is text: postgres.js would make a `date` a JS Date. */
export async function mvAccuracy(limit = 14): Promise<MvAccuracyRow[]> {
  return sql<MvAccuracyRow[]>`
    select to_char(target_day, 'YYYY-MM-DD') as target_day, scored, unscorable,
           directional, direction_hits, mae_pct, baseline_mae_pct, mae_eur, baseline_mae_eur
    from rehoboam.web_mv_accuracy
    order by target_day desc
    limit ${limit}
  `;
}

export type SessionRow = {
  session_id: string;
  app: string;
  mode: string;
  dry_run: number;
  started_at: number;
  duration_s: number;
  errors: number;
  error_text: string;
  lineup_result: string | null;
  predictions_written: number | null;
  integrity_rules: string[];
  integrity_details: Record<string, string>;
  league_state_squads: number | null;
  requests: number | null;
  failed: number | null;
  status_written: number | null;
  universe_size: number | null;
  stopped_by: string | null;
  league_teams: number | null;
  league_fixtures: number | null;
  league_failed: number | null;
};

/**
 * `worst` stores player ids; a person cannot act on "11006". Resolve names in
 * one query for every id on the page, and fall back to the id if a player has
 * left the universe.
 */
export async function playerNames(ids: string[]): Promise<Record<string, string>> {
  if (ids.length === 0) return {};
  const rows = await sql<{ player_id: string; name: string }[]>`
    select player_id, name from rehoboam.web_players where player_id = any(${ids})
  `;
  return Object.fromEntries(rows.map((r) => [r.player_id, r.name]));
}

export async function sessions(limit = 30): Promise<SessionRow[]> {
  return sql<SessionRow[]>`
    select * from rehoboam.web_session_summary
    order by started_at desc
    limit ${limit}
  `;
}
