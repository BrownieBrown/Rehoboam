import { describe, expect, it } from "vitest";
import { gateDecision, type GateDecision } from "./gate";

// The whole table, one row per combination of hasUser/allowed/isPublic, plus
// the path === "/login" vs. another public path distinction that only
// matters on the signed-in-and-allowed row. `path` for the isPublic=false
// rows is an arbitrary private page — the decision must not depend on which
// private page it is.
const LOGIN = "/login";
const OTHER_PUBLIC = "/auth/callback";
const PRIVATE = "/squad";

type Row = { hasUser: boolean; allowed: boolean; isPublic: boolean; path: string; want: GateDecision };

const TABLE: Row[] = [
  // no user, public path -> next (both public paths, both allowed values —
  // `allowed` is meaningless without a user, decision must ignore it)
  { hasUser: false, allowed: false, isPublic: true, path: LOGIN, want: "next" },
  { hasUser: false, allowed: true, isPublic: true, path: LOGIN, want: "next" },
  { hasUser: false, allowed: false, isPublic: true, path: OTHER_PUBLIC, want: "next" },
  { hasUser: false, allowed: true, isPublic: true, path: OTHER_PUBLIC, want: "next" },

  // no user, private path -> to-login
  { hasUser: false, allowed: false, isPublic: false, path: PRIVATE, want: "to-login" },
  { hasUser: false, allowed: true, isPublic: false, path: PRIVATE, want: "to-login" },

  // signed-in, allowed, on /login -> to-home
  { hasUser: true, allowed: true, isPublic: true, path: LOGIN, want: "to-home" },

  // signed-in, allowed, anywhere else (public or private) -> next
  { hasUser: true, allowed: true, isPublic: true, path: OTHER_PUBLIC, want: "next" },
  { hasUser: true, allowed: true, isPublic: false, path: PRIVATE, want: "next" },

  // signed-in, NOT allowed, any path (public or private, including /login
  // itself) -> sign-out. Never to-home, never next.
  { hasUser: true, allowed: false, isPublic: true, path: LOGIN, want: "sign-out" },
  { hasUser: true, allowed: false, isPublic: true, path: OTHER_PUBLIC, want: "sign-out" },
  { hasUser: true, allowed: false, isPublic: false, path: PRIVATE, want: "sign-out" },
];

describe("gateDecision", () => {
  it.each(TABLE)(
    "hasUser=$hasUser allowed=$allowed isPublic=$isPublic path=$path -> $want",
    ({ hasUser, allowed, isPublic, path, want }) => {
      expect(gateDecision({ hasUser, allowed, isPublic, path })).toBe(want);
    },
  );

  it("covers every hasUser x allowed x isPublic combination (8 of 8)", () => {
    const seen = new Set(TABLE.map((r) => `${r.hasUser}:${r.allowed}:${r.isPublic}`));
    for (const hasUser of [true, false]) {
      for (const allowed of [true, false]) {
        for (const isPublic of [true, false]) {
          expect(seen.has(`${hasUser}:${allowed}:${isPublic}`)).toBe(true);
        }
      }
    }
  });

  it("never sends a not-allowed user to '/' or lets one through as next/to-home", () => {
    for (const isPublic of [true, false]) {
      for (const path of [LOGIN, OTHER_PUBLIC, PRIVATE]) {
        const decision = gateDecision({ hasUser: true, allowed: false, isPublic, path });
        expect(decision).toBe("sign-out");
        expect(decision).not.toBe("next");
        expect(decision).not.toBe("to-home");
      }
    }
  });
});
