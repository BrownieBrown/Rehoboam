/**
 * Guards the `next` query param the auth callback redirects to.
 *
 * A prefix check alone is not enough: the WHATWG URL parser that `new URL()`
 * uses normalises a backslash to a forward slash for http(s) schemes and
 * strips tab/newline characters outright, so a string like `"/\\evil.com"`
 * starts with a single `/` yet still resolves to `https://evil.com/`. Resolve
 * `raw` against `origin` the same way the redirect will, then compare
 * origins — that is the property that actually matters, not the shape of the
 * input string.
 */
export function safeNext(raw: string | null, origin: string): string {
  if (!raw || !raw.startsWith("/")) return "/";
  try {
    const url = new URL(raw, origin);
    if (url.origin !== origin) return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}
