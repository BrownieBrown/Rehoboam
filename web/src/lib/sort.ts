/**
 * A sort column is an SQL identifier, not a value, so it can never be a bound
 * parameter. This whitelist is the only thing standing between a query string
 * and the database - every page passes its own allowed list.
 *
 * Generic so that a caller passing a `readonly [...] as const` list (e.g.
 * `PLAYER_SORTS`) gets back a narrowed literal union instead of a plain
 * `string` -- that's what lets `PlayerList.tsx`'s `rankedFigureText` switch
 * over every sort key and have TypeScript catch a future key nobody handled.
 * The `includes` check still runs against the raw list at runtime; the cast
 * only widens the type an already-checked value is treated as.
 */
export function sortKey<T extends string>(
  raw: string | undefined,
  allowed: readonly T[],
  fallback: T,
): T {
  return raw && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback;
}

export function sortDir(raw: string | undefined): "asc" | "desc" {
  return raw === "asc" ? "asc" : "desc";
}
