import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
// The real adapter a route handler's `cookies()` returns, and the real
// function Next's app-route module uses to attach its writes to the
// Response it returns (next/dist/server/route-modules/app-route/module.js).
// Using both here — instead of mocking `./supabase` — means the actual
// cookie-write behaviour (exchange, then an overriding sign-out clear) runs
// under test, the same way the real Route Handler runtime exercises it.
import {
  MutableRequestCookiesAdapter,
  appendMutableCookies,
} from "next/dist/server/web/spec-extension/adapters/request-cookies.js";
import { RequestCookies } from "next/dist/server/web/spec-extension/cookies.js";

let store: ReturnType<typeof MutableRequestCookiesAdapter.wrap>;
vi.mock("next/headers", () => ({
  cookies: async () => store,
  headers: async () => new Headers(),
}));

// `vi.mock` above is hoisted by Vitest above every import in this file
// (including this one), so `route.ts`'s own `import { cookies } from
// "next/headers"` resolves to the mock, not the real module.
import { GET } from "./route";

const REF = "abcdefgh";
const KEY = `sb-${REF}-auth-token`;
const b64url = (s: string) => Buffer.from(s).toString("base64url");
const jwt = (exp: number) =>
  `${b64url('{"alg":"HS256","typ":"JWT"}')}.${b64url(JSON.stringify({ sub: "u1", exp }))}.sig`;

let calls: string[] = [];

function installFetch(email: string, logout: "ok" | "500" = "ok") {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const u = String(url);
      calls.push(u);
      if (u.includes("grant_type=pkce")) {
        const exp = Math.floor(Date.now() / 1000) + 3600;
        return new Response(
          JSON.stringify({
            access_token: jwt(exp),
            refresh_token: "rt",
            expires_in: 3600,
            expires_at: exp,
            token_type: "bearer",
            user: { id: "u1", aud: "authenticated", email },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (u.includes("/logout")) {
        return logout === "ok"
          ? new Response(null, { status: 204 })
          : new Response('{"msg":"x"}', { status: 500, headers: { "content-type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }),
  );
}

/** Builds the request, wires a real cookies() store for it, calls GET, and
 * merges the route's queued cookie writes onto the returned response — the
 * same two steps Next's app-route module performs in production. */
async function run(url: string) {
  const verifier = `${KEY}-code-verifier=base64-${b64url(JSON.stringify("verifier123"))}`;
  const req = new NextRequest(url, { headers: { cookie: verifier } });
  store = MutableRequestCookiesAdapter.wrap(new RequestCookies(req.headers));
  const res = await GET(req);
  const headers = new Headers(res.headers);
  appendMutableCookies(headers, store);
  return { res, setCookie: headers.getSetCookie() };
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = `https://${REF}.supabase.co`;
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon";
  process.env.ALLOWED_EMAILS = "owner@example.com";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("auth callback", () => {
  // Rule 7: a not-allowed account never leaves the callback holding a
  // session, whether or not Supabase's own /logout call succeeds.
  for (const logout of ["ok", "500"] as const) {
    it(`not-allowed (logout=${logout}): only cookie deletions leave, redirected to /login?error=not-allowed`, async () => {
      installFetch("evil@example.com", logout);
      const { res, setCookie } = await run("https://site.test/auth/callback?code=c1&next=/squad");
      expect(res.headers.get("location")).toBe("https://site.test/login?error=not-allowed");
      // A large session is split into `…-auth-token.0`, `.1`, … cookies.
      const auth = setCookie.filter((c) => c.startsWith(`${KEY}=`) || c.startsWith(`${KEY}.`));
      expect(auth.length).toBe(1);
      expect(auth[0]).toMatch(/^sb-abcdefgh-auth-token=;.*Max-Age=0/);
      // Local scope only: never the account's sessions on other devices.
      const signOuts = calls.filter((u) => u.includes("/logout"));
      expect(signOuts.map((u) => new URL(u).searchParams.get("scope"))).toEqual(["local"]);
    });
  }

  it("allowed: session cookie set, redirected to the safeNext URL", async () => {
    installFetch("Owner@Example.com");
    const { res, setCookie } = await run("https://site.test/auth/callback?code=c1&next=/squad");
    expect(res.headers.get("location")).toBe("https://site.test/squad");
    expect(setCookie.some((c) => c.startsWith(`${KEY}=base64-`))).toBe(true);
  });

  it("allowed, hostile next: falls back to the origin root", async () => {
    installFetch("owner@example.com");
    const { res } = await run("https://site.test/auth/callback?code=c1&next=//evil.com");
    expect(res.headers.get("location")).toBe("https://site.test/");
  });
});
