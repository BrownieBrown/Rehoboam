/**
 * Who a Market listing comes from. Kickbase's own listings are the default
 * view; managers' listings (ours included) are one click away.
 */
export type SellerScope = "kickbase" | "managers" | "all";

/**
 * The seller name `rehoboam.web_market` gives a listing with no known
 * manager behind it (`coalesce(m.name, 'Kickbase')`). On 2026-09-17 every
 * listing with a seller id matched a manager and no manager was named
 * "Kickbase", so the name is exact. A listing whose seller has left the
 * league would also read "Kickbase", here and in the Seller column alike.
 */
export const KICKBASE_SELLER = "Kickbase";

/** `?from=` to a scope. Absent or unknown means the default, Kickbase. */
export function sellerScope(raw: string | undefined): SellerScope {
  return raw === "managers" || raw === "all" ? raw : "kickbase";
}

/** The listings in `scope`, in the order given. */
export function bySeller<T extends { seller: string }>(rows: T[], scope: SellerScope): T[] {
  if (scope === "all") return rows;
  const wantKickbase = scope === "kickbase";
  return rows.filter((r) => (r.seller === KICKBASE_SELLER) === wantKickbase);
}
