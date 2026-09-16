/**
 * Guards the `next` query param the auth callback redirects to.
 *
 * `new URL(str, base)` ignores `base` whenever `str` already parses as an
 * absolute or protocol-relative URL (`https://evil.example/x`, or the
 * protocol-relative `//evil.example`, which the browser resolves against its
 * own current protocol). Handing either straight to `new URL()` sends the
 * browser off-origin right after a successful code exchange. Only a string
 * that starts with a single `/` (rooted, same-origin, not `//...`) is safe
 * to redirect to; anything else falls back to `/`.
 */
export function safeNext(raw: string | null): string {
  if (raw && raw.startsWith("/") && !raw.startsWith("//")) return raw;
  return "/";
}
