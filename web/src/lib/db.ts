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
  });

if (process.env.NODE_ENV !== "production") globalThis.__rehoboamSql = sql;
