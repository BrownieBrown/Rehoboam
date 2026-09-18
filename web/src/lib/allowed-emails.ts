/**
 * The site is single-owner: `ALLOWED_EMAILS` (server-only, never
 * `NEXT_PUBLIC_…`) is the one-line allowlist. Pure and env-agnostic on
 * purpose — every caller reads `process.env.ALLOWED_EMAILS` itself (in
 * middleware, `auth.ts`, the callback route) and hands the raw string in,
 * so this module never touches `process.env` and stays trivially testable.
 */

/**
 * Splits on commas, trims, lower-cases, and drops empty entries. An unset,
 * empty, or comma/space-only `raw` parses to an empty set — never everyone.
 */
export function parseAllowList(raw: string | undefined): Set<string> {
  if (!raw) return new Set();
  const entries = raw
    .split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter((entry) => entry.length > 0);
  return new Set(entries);
}

/**
 * Fail closed, exact match only. An empty allowlist (unset, empty, or
 * comma-only `raw`) refuses every address, including a correctly-typed one —
 * a misconfigured deploy must lock everyone out, never let everyone in.
 * Match is the whole string, after trimming and lower-casing both sides: no
 * substring, prefix, suffix, or wildcard match, so a homoglyph, a longer
 * string that merely contains the address, or a near-miss domain all refuse.
 */
export function isAllowedEmail(email: string | null | undefined, raw: string | undefined): boolean {
  if (!email) return false;
  const list = parseAllowList(raw);
  if (list.size === 0) return false;
  return list.has(email.trim().toLowerCase());
}
