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
