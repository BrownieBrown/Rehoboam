import "server-only";
import { cache } from "react";
import { sql } from "./db";

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
 * The sidebar and header need these on every route, including the login page
 * where there is no session — so this never throws; a failure renders dashes.
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
};

export const PLAYER_SORTS = [
  "name",
  "position",
  "market_value",
  "trend_24h_pct",
  "trend_7d_pct",
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

export async function players(opts: {
  sort: string;
  dir: "asc" | "desc";
  position?: string;
  owner?: "mine" | "free";
  club?: string;
  q?: string;
  limit?: number;
}): Promise<PlayerRow[]> {
  // "mine" needs our manager name; when there is none, the filter matches nothing.
  const mine = opts.owner === "mine" ? ((await selfName()) ?? "") : null;
  const free = opts.owner === "free";
  const like = opts.q ? `%${opts.q}%` : null;
  // Same truthy guard as `like` above: the club filter is driven by a plain
  // GET <select>, and its "All clubs" option submits `club=` (empty string,
  // not absent) - `?? null` alone would turn that into `team = ''`, which
  // matches nothing, silently breaking the reset-to-all-clubs path.
  const club = opts.club ? opts.club : null;
  // sql.unsafe(opts.sort) is safe ONLY because every caller runs opts.sort
  // through sortKey() against its own allow-list first (see sort.ts) - a
  // sort column is an identifier, not a value, so it can never be a bound
  // parameter. Never pass a raw search param to it.
  return sql<PlayerRow[]>`
    select * from rehoboam.web_players
    where (${opts.position ?? null}::text is null or position = ${opts.position ?? null})
      and (${club}::text is null or team = ${club})
      and (${like}::text is null or name ilike ${like} or team ilike ${like})
      and (${mine}::text is null or owner = ${mine})
      and (${free} = false or owner = 'Kickbase')
    order by ${sql.unsafe(opts.sort)} ${opts.dir === "asc" ? sql`asc` : sql`desc`} nulls last,
             player_id asc
    limit ${opts.limit ?? 50}
  `;
}
