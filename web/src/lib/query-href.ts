export type Params = Record<string, string | undefined>;

/**
 * `path` with every entry in `params` reproduced as-is, then `overrides`
 * applied on top: a `null` override clears that key, anything else sets it.
 * Filter links use it so toggling one filter never drops another (or the
 * sort). The page number is dropped for every override except opening a
 * player (`overrides.player` set to an id, not `null`): a changed filter or
 * sort still starts again at page 1, but opening a player from page 2 must
 * not silently return the list to page 1 underneath the panel -- the
 * clicked row would no longer be in the visible list to amber-mark (review
 * round 2, finding 4). Closing the panel (`player: null`) and every other
 * override are unaffected, deliberately.
 */
export function hrefFor(path: string, params: Params, overrides: Record<string, string | null>): string {
  const keepPage = typeof overrides.player === "string";
  const next = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (!v) continue;
    if (k === "page" && !keepPage) continue;
    next.set(k, v);
  }
  for (const [k, v] of Object.entries(overrides)) {
    if (v === null) next.delete(k);
    else next.set(k, v);
  }
  const qs = next.toString();
  return qs ? `${path}?${qs}` : path;
}
