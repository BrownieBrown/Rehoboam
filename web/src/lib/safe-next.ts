/**
 * Guards the `next` query param the auth callback redirects to. Returns a
 * fully resolved, absolute, same-origin URL string — never a bare path.
 *
 * Two things can go wrong, and both are about *re-parsing*, not the shape
 * of the input string:
 *
 * 1. A prefix check alone is not enough: the WHATWG URL parser that
 *    `new URL()` uses normalises a backslash to a forward slash for http(s)
 *    schemes and strips tab/newline characters outright, so a string like
 *    `"/\\evil.com"` starts with a single `/` yet still resolves to
 *    `https://evil.com/`. Resolving `raw` against `origin` and comparing
 *    origins (rather than pattern-matching the string) handles this.
 * 2. Handing back a *path* is not enough either: `new URL("/..//evil.com",
 *    origin)` resolves the `..` segment and correctly lands back on
 *    `origin`, but the resulting pathname is `//evil.com` — protocol-relative
 *    on a *second* parse. Since the caller (the auth callback) does its own
 *    `new URL(next, origin)`, a same-origin path from this function can
 *    still leave the origin under that second parse. Returning the already-
 *    resolved `url.href` instead of `url.pathname + url.search + url.hash`
 *    means there is nothing left to re-interpret: an absolute same-origin
 *    href stays on that exact origin no matter how many more times it gets
 *    parsed.
 */
export function safeNext(raw: string | null, origin: string): string {
  const fallback = `${origin}/`;
  if (!raw || !raw.startsWith("/")) return fallback;
  try {
    const url = new URL(raw, origin);
    if (url.origin !== origin) return fallback;
    return url.href;
  } catch {
    return fallback;
  }
}
