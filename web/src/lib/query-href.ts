export type Params = Record<string, string | undefined>;

/**
 * `path` with every entry in `params` reproduced as-is, then `overrides`
 * applied on top: a `null` override clears that key, anything else sets it.
 * Filter links use it so toggling one filter never drops another (or the
 * sort). The page number is the one thing dropped: a changed filter starts
 * again at page 1.
 */
export function hrefFor(path: string, params: Params, overrides: Record<string, string | null>): string {
  const next = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v && k !== "page") next.set(k, v);
  for (const [k, v] of Object.entries(overrides)) {
    if (v === null) next.delete(k);
    else next.set(k, v);
  }
  const qs = next.toString();
  return qs ? `${path}?${qs}` : path;
}
