/**
 * A sort column is an SQL identifier, not a value, so it can never be a bound
 * parameter. This whitelist is the only thing standing between a query string
 * and the database - every page passes its own allowed list.
 */
export function sortKey(raw: string | undefined, allowed: string[], fallback: string): string {
  return raw && allowed.includes(raw) ? raw : fallback;
}

export function sortDir(raw: string | undefined): "asc" | "desc" {
  return raw === "asc" ? "asc" : "desc";
}
