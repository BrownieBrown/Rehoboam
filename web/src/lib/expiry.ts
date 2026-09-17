/**
 * Listings whose clock runs out within `hours` of `now`, in the order given.
 * A listing with no expiry never qualifies; one already past its expiry
 * does, and its countdown reads "expired".
 */
export function expiringWithin<T extends { expires_at: number | null }>(
  rows: T[],
  hours: number,
  now: number,
): T[] {
  const cutoff = now + hours * 3600;
  return rows.filter((r) => r.expires_at !== null && r.expires_at <= cutoff);
}

/**
 * The Market header's count. `total` is every listing in the snapshot and
 * `shown` what the filter kept, so the line stays true when the filter keeps
 * nothing: "0 of 48 listings expiring under 6 h".
 */
export function listingsLine(shown: number, total: number, hours?: number): string {
  const noun = total === 1 ? "listing" : "listings";
  if (hours === undefined) return `${total} ${noun}`;
  return `${shown} of ${total} ${noun} expiring under ${hours} h`;
}
