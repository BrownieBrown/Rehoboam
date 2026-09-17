import "server-only";
import postgres from "postgres";

/** The bot role's pooler URL. Server-side only; never a NEXT_PUBLIC_ variable. */
export function dsn(): string {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error(
      "DATABASE_URL is not set. The site reads the store as the rehoboam_bot role " +
        "through the Supabase transaction pooler; set it in the Vercel project.",
    );
  }
  return url;
}

declare global {
  // eslint-disable-next-line no-var
  var __rehoboamSql: ReturnType<typeof postgres> | undefined;
}

/**
 * `types` is not optional. postgres.js parses int2/int4/float/bool/json
 * by default but returns `bigint` (oid 20) and `numeric` (oid 1700) as
 * strings, and the views are full of both: market values, asks and budgets
 * are bigint columns, `player_table`'s `sum()`/`count()` columns are bigint,
 * and every `round(…::numeric, n)` column is numeric. A string there reaches
 * `num()` as `"123".toFixed` and the page throws. Both parse to a plain
 * `number`: an integer stays exact because every one here (euros, points,
 * counts) is far below 2^53, and a rounded value still prints the digits it
 * was rounded to.
 */
const types = {
  bigint: {
    to: 20,
    from: [20],
    parse: (x: string) => Number(x),
    serialize: (x: number) => String(x),
  },
  numeric: {
    to: 1700,
    from: [1700],
    parse: (x: string) => Number(x),
    serialize: (x: number) => String(x),
  },
};

/**
 * One client per process, reused across requests.
 *
 * `prepare: false` is not optional: the Supabase transaction pooler rejects
 * prepared statements, which is the same reason the Python store passes
 * `prepare_threshold=None`. `max: 3` keeps a serverless fleet from exhausting
 * the pooler's connection budget; the site is read-only and low-traffic.
 */
export const sql =
  globalThis.__rehoboamSql ??
  postgres(dsn(), {
    prepare: false,
    max: 3,
    idle_timeout: 20,
    connect_timeout: 10,
    transform: { undefined: null },
    types,
  });

if (process.env.NODE_ENV !== "production") globalThis.__rehoboamSql = sql;
