import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";

// Exercises the real `@supabase/ssr` + auth-js cookie machinery against a
// stubbed global `fetch` — never the Supabase client itself — so the actual
// cookie-write behaviour (refresh rotation, sign-out clearing) runs under
// test. No network: every Supabase Auth endpoint the middleware can reach
// is answered by this stub.
const REF = "abcdefgh";
const KEY = `sb-${REF}-auth-token`;
const b64url = (s: string) => Buffer.from(s).toString("base64url");
const jwt = (exp: number) =>
  `${b64url('{"alg":"HS256","typ":"JWT"}')}.${b64url(
    JSON.stringify({ sub: "u1", exp, session_id: "s1" }),
  )}.sig`;

/** A `sb-<ref>-auth-token` cookie header for a session expiring in `expiresInSec`. */
function sessionCookie(expiresInSec: number) {
  const exp = Math.floor(Date.now() / 1000) + expiresInSec;
  const session = {
    access_token: jwt(exp),
    refresh_token: "rt-old",
    expires_at: exp,
    expires_in: expiresInSec,
    token_type: "bearer",
    user: { id: "u1", email: "x" },
  };
  return `${KEY}=base64-${b64url(JSON.stringify(session))}`;
}

type Mode = { email: string; logout?: "ok" | "500"; refresh?: "ok" | "invalid" };

function installFetch(mode: Mode) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/auth/v1/user")) {
        return new Response(
          JSON.stringify({ id: "u1", aud: "authenticated", email: mode.email }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (u.includes("/auth/v1/logout")) {
        return mode.logout === "500"
          ? new Response(JSON.stringify({ msg: "boom" }), {
              status: 500,
              headers: { "content-type": "application/json" },
            })
          : new Response(null, { status: 204 });
      }
      if (u.includes("grant_type=refresh_token")) {
        if (mode.refresh === "invalid") {
          return new Response(
            JSON.stringify({
              code: 400,
              error_code: "refresh_token_already_used",
              msg: "Invalid Refresh Token: Already Used",
            }),
            { status: 400, headers: { "content-type": "application/json" } },
          );
        }
        const exp = Math.floor(Date.now() / 1000) + 3600;
        return new Response(
          JSON.stringify({
            access_token: jwt(exp),
            refresh_token: "rt-NEW",
            expires_in: 3600,
            expires_at: exp,
            token_type: "bearer",
            user: { id: "u1", aud: "authenticated", email: mode.email },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("{}", { status: 404 });
    }),
  );
}

function req(path: string, cookie?: string) {
  return new NextRequest(`https://site.test${path}`, { headers: cookie ? { cookie } : {} });
}

const setCookies = (r: Response) => r.headers.getSetCookie();

beforeEach(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = `https://${REF}.supabase.co`;
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon";
  process.env.ALLOWED_EMAILS = "owner@example.com";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("middleware", () => {
  // Rule 5: a not-allowed user is signed out and the deleted cookie reaches
  // the redirect, regardless of whether Supabase's own /logout succeeds.
  for (const logout of ["ok", "500"] as const) {
    it(`signs a not-allowed user out (logout=${logout}) and carries the cleared cookie on the redirect`, async () => {
      installFetch({ email: "evil@example.com", logout });
      const res = await middleware(req("/squad", sessionCookie(3600)));
      expect(res.status).toBe(307);
      expect(res.headers.get("location")).toBe("https://site.test/login?error=not-allowed");
      const cleared = setCookies(res).find((c) => c.startsWith(`${KEY}=`));
      expect(cleared).toBeDefined();
      expect(cleared).toMatch(/^sb-abcdefgh-auth-token=;.*Max-Age=0/);
    });
  }

  // Rule 5 / fix 1: this is the case that fails without redirectWithCookies
  // on the to-home branch — a token auth-js rotates inside getUser() (the
  // access token has under 90s left) must still reach the browser.
  it("carries a rotated token cookie on the to-home redirect after a near-expiry refresh", async () => {
    installFetch({ email: "owner@example.com" });
    const res = await middleware(req("/login", sessionCookie(30)));
    expect(res.headers.get("location")).toBe("https://site.test/");
    const rotated = setCookies(res).find((c) => c.startsWith(`${KEY}=`));
    expect(rotated).toBeDefined();
    expect(rotated).toMatch(new RegExp(`^${KEY}=base64-`));
  });

  // Same fix, the to-login branch: a dead refresh token's cookie deletion
  // must also reach the browser, not just sign-out's.
  it("carries the cleared cookie on the to-login redirect after a dead refresh token", async () => {
    installFetch({ email: "owner@example.com", refresh: "invalid" });
    const res = await middleware(req("/squad", sessionCookie(-10)));
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/squad");
    const cleared = setCookies(res).find((c) => c.startsWith(`${KEY}=`));
    expect(cleared).toBeDefined();
    expect(cleared).toMatch(/Max-Age=0/);
  });

  // Rule: no session at all on a private path -> to-login, carrying `next`.
  it("sends an anonymous user on a private path to /login?next=…", async () => {
    installFetch({ email: "n/a" });
    const res = await middleware(req("/squad"));
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/squad");
  });

  // Rule 1: an empty/unset ALLOWED_EMAILS fails closed, even for the
  // address that would otherwise be the owner.
  it("fails closed when ALLOWED_EMAILS is unset: a signed-in user is signed out", async () => {
    delete process.env.ALLOWED_EMAILS;
    installFetch({ email: "owner@example.com" });
    const res = await middleware(req("/", sessionCookie(3600)));
    expect(res.headers.get("location")).toBe("https://site.test/login?error=not-allowed");
  });
});
