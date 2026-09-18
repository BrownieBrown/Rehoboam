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
  /** Last season's average, from `player_table`'s `prev`/`hist` CTEs --
   * already in every `web_players` row, just never typed until the panel
   * needed it. */
  avg_points_prev: number | null;
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
] as const;

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

/** One player's row from `web_players`, for the overlay's header and "our numbers"
 * grid. `total` is not meaningful here — it only exists so this shares `PlayerRow`'s
 * shape with the table query above — and is always 0. */
export async function playerRow(playerId: string): Promise<PlayerRow | null> {
  const [row] = await sql<PlayerRow[]>`
    select *, 0 as total from rehoboam.web_players where player_id = ${playerId}
  `;
  return row ?? null;
}

export type PlayerProfile = PlayerRow & {
  trend_24h_eur: number | null;
  trend_7d_eur: number | null;
  goals: number | null;
  assists: number | null;
  yellow_cards: number | null;
  red_cards: number | null;
  seconds_played: number | null;
  season_points: number | null;
  season_average: number | null;
  rank_overall: number | null;
  rank_position: number | null;
  /** Kickbase's `st` availability code — 0 is fit. `availability.ts` names it. */
  availability: number | null;
  /** How many players carry a rank at all, for the "of N" under each rank. */
  ranked_overall_total: number | null;
  ranked_position_total: number | null;
  /** His club's line in the newest stored matchday of the league table. */
  club_place: number | null;
  club_points: number | null;
  club_goal_difference: number | null;
};

/** One player's row from `web_player_profile`: `web_players`' columns plus the
 * season stats, the market-value move in euros, and his rank -- what the
 * player panel's header needs in one query. `total` is not meaningful here --
 * see `playerRow` above -- and is always 0. */
export async function playerProfile(playerId: string): Promise<PlayerProfile | null> {
  const [row] = await sql<PlayerProfile[]>`
    select *, 0 as total from rehoboam.web_player_profile where player_id = ${playerId}
  `;
  return row ?? null;
}

export type PlayerSeason = {
  season: string;
  appearances: number;
  starts: number;
  points: number;
  avg_points: number | null;
  median_points: number | null;
  best_points: number | null;
  minutes: number | null;
};

/** Every season the store has for this player, newest first. */
export async function playerSeasons(playerId: string): Promise<PlayerSeason[]> {
  return sql<PlayerSeason[]>`
    select * from rehoboam.web_player_seasons
    where player_id = ${playerId}
    order by season desc
  `;
}

export type PlayerMatch = {
  season: string;
  day_number: number;
  match_date: string | null;
  points: number | null;
  minutes: number | null;
  status: number | null;
  is_home: number | null;
  opponent: string | null;
  /** ISO-8601 text, not a JS Date: postgres.js would otherwise turn the
   * view's `timestamptz` column into one, the same reason `playerMv` does
   * this for its `day`. Null when the stored `match_date` text didn't parse
   * into a real timestamp -- the row still appears, dashed, not dropped. */
  match_at: string | null;
};

/**
 * His newest *played* matches. `player_match_history` holds the whole
 * fixture list, future matchdays included, as rows with a real future date,
 * `status = 0`, `points = 0`; ordering by `season desc, day_number desc`
 * alone would show next season's remaining fixtures ahead of the matches he
 * actually played. `match_at is null` keeps a row whose date text never
 * parsed (unplayed history, or malformed text) rather than hiding it.
 *
 * Filtering to played matchdays only narrows *which* rows come back -- it
 * does not say whether he was on the pitch for one that did. `points = 0`
 * here means either "played and scored nothing" or "an unused sub /
 * out of the squad, so there's nothing to score": `status` is what tells
 * those apart (5 started, 3 came on, everything else did not play), which
 * is exactly what `formEntries` (`./form.ts`) reads it for.
 */
export async function playerMatches(playerId: string, limit = 12): Promise<PlayerMatch[]> {
  return sql<PlayerMatch[]>`
    select season, day_number, match_date, points, minutes, status, is_home, opponent,
      to_char(match_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as match_at
    from rehoboam.web_player_matches
    where player_id = ${playerId} and (match_at is null or match_at <= now())
    order by match_at desc nulls last, season desc, day_number desc
    limit ${limit}
  `;
}

/**
 * Every stored matchday of one season for one player, played or not -- lets
 * the panel draw a season strip with blanks for matchdays not yet played.
 * `match_at` is ISO-8601 text, not a JS Date, the same reason `playerMatches`
 * does it.
 */
export async function playerSeasonGrid(
  playerId: string,
  season: string,
): Promise<{ day_number: number; points: number | null; match_at: string | null }[]> {
  return sql<{ day_number: number; points: number | null; match_at: string | null }[]>`
    select day_number, points,
      to_char(match_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as match_at
    from rehoboam.web_player_matches
    where player_id = ${playerId} and season = ${season}
    order by day_number asc
  `;
}

export type PlayerMvPoint = { day: string; market_value: number };

/** His market-value history over the last `days`. `day` arrives as text
 * (`to_char`), not a JS Date — the same reason `mvAccuracy` does it. */
export async function playerMv(playerId: string, days = 180): Promise<PlayerMvPoint[]> {
  return sql<PlayerMvPoint[]>`
    select to_char(day, 'YYYY-MM-DD') as day, market_value
    from rehoboam.web_player_mv
    where player_id = ${playerId} and day >= current_date - ${days}::int
    order by day asc
  `;
}

export type PlayerFixture = {
  season: string;
  day_number: number;
  /** ISO-8601 text, not a JS Date — the same reason `playerMatches` does it. */
  kickoff_at: string | null;
  is_home: boolean;
  opponent: string | null;
  opponent_place: number | null;
};

/** His next `limit` matches, soonest first. Empty for a player with no club,
 * and for one whose club has no upcoming fixture stored. */
export async function playerFixtures(playerId: string, limit = 3): Promise<PlayerFixture[]> {
  return sql<PlayerFixture[]>`
    select season, day_number, is_home, opponent, opponent_place,
      to_char(to_timestamp(kickoff) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as kickoff_at
    from rehoboam.web_player_fixtures
    where player_id = ${playerId}
    order by kickoff asc
    limit ${limit}::int
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
  /** Migration 010: the same two numbers the Players page shows. */
  trend_24h_pct: number | null;
  points_per_million: number | null;
};

export const MARKET_SORTS = [
  "name",
  "position",
  "ask",
  "market_value",
  "next_mv_pct",
  "fair_price",
  "trend_24h_pct",
  "points_per_million",
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
