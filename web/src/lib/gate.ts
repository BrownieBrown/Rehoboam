export type GateDecision = "next" | "to-login" | "to-home" | "sign-out";

export interface GateInput {
  hasUser: boolean;
  allowed: boolean;
  isPublic: boolean;
  path: string;
}

/**
 * The middleware's one decision table: who gets through, who gets sent to
 * `/login`, who gets bounced away from `/login`, and who gets signed out.
 * `requireSession()` (`lib/auth.ts`) enforces the same not-allowed rule as
 * a second lock, but redirects directly rather than going through this
 * table — every page that calls it is already private, so it never needs
 * `to-home` or the plain `next` case.
 *
 * The not-allowed check comes first, before anything else, so a signed-in
 * but disallowed account is never confused with "no user" (which would send
 * it to `/login` without clearing its session) and is never let through
 * because it happens to be on a public path — `sign-out` fires for a
 * not-allowed user on *any* path, `/login` and `/auth/callback` included.
 * `sign-out`'s caller always redirects to `/login`, and a not-allowed user
 * is never routed to `/` (which would just bounce back to `/login`) — so
 * there is no loop.
 */
export function gateDecision({ hasUser, allowed, isPublic, path }: GateInput): GateDecision {
  if (hasUser && !allowed) return "sign-out";
  if (!hasUser) return isPublic ? "next" : "to-login";
  return path === "/login" ? "to-home" : "next";
}
