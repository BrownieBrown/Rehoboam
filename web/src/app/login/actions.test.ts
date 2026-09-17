import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
// Same approach as the callback route's test: the real cookies() adapter and
// the real function Next uses to attach its writes to the response, so the
// actual @supabase/ssr + auth-js cookie behaviour runs under test against a
// stubbed `fetch` — never a mocked Supabase client.
import {
  MutableRequestCookiesAdapter,
  appendMutableCookies,
} from "next/dist/server/web/spec-extension/adapters/request-cookies.js";
import { RequestCookies } from "next/dist/server/web/spec-extension/cookies.js";
import { isRedirectError } from "next/dist/client/components/redirect-error.js";
import { getURLFromRedirectError } from "next/dist/client/components/redirect.js";

let store: ReturnType<typeof MutableRequestCookiesAdapter.wrap>;
vi.mock("next/headers", () => ({
  cookies: async () => store,
  headers: async () => new Headers(),
}));
// Next's bundler resolves `next/navigation` to its react-server entry for
// server actions; plain Vitest would load the client entry, which needs a
// React client runtime. Point it at the same server entry Next uses, so the
// real `redirect()` (and its NEXT_REDIRECT error) runs here.
vi.mock("next/navigation", () => import("next/dist/api/navigation.react-server.js"));

import { signInWithPassword } from "./actions";

const REF = "abcdefgh";
const KEY = `sb-${REF}-auth-token`;
const b64url = (s: string) => Buffer.from(s).toString("base64url");
const jwt = (exp: number) =>
  `${b64url('{"alg":"HS256","typ":"JWT"}')}.${b64url(JSON.stringify({ sub: "u1", exp }))}.sig`;

type Token =
  | { kind: "ok"; email: string }
  | { kind: "fail"; status: number; code?: string };

let calls: string[] = [];

function installFetch(token: Token, logout: "ok" | "500" = "ok") {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      calls.push(u);
      if (u.includes("grant_type=password")) {
        if (token.kind === "fail") {
          // Supabase's current error shape: the version header plus a
          // string `code`, which is what auth-js turns into `error.code`.
          return new Response(
            JSON.stringify({ code: token.code ?? token.status, message: "nope" }),
            {
              status: token.status,
              headers: {
                "content-type": "application/json",
                ...(token.code ? { "x-supabase-api-version": "2024-01-01" } : {}),
              },
            },
          );
        }
        const body = JSON.parse(String(init?.body ?? "{}"));
        expect(body.password).toBe(" pass word ");
        const exp = Math.floor(Date.now() / 1000) + 3600;
        return new Response(
          JSON.stringify({
            access_token: jwt(exp),
            refresh_token: "rt",
            expires_in: 3600,
            expires_at: exp,
            token_type: "bearer",
            user: { id: "u1", aud: "authenticated", email: token.email },
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

function form(email: string, password: string) {
  const f = new FormData();
  f.set("email", email);
  f.set("password", password);
  return f;
}

/** Calls the action with a fresh cookie store and returns either its message
 * or the URL it redirected to, plus the Set-Cookie headers it queued. */
async function run(f: FormData) {
  const req = new NextRequest("https://site.test/login", { method: "POST" });
  store = MutableRequestCookiesAdapter.wrap(new RequestCookies(req.headers));
  let message: string | null = null;
  let email: string | undefined;
  let location: string | null = null;
  try {
    ({ message, email } = await signInWithPassword({ message: "" }, f));
  } catch (err) {
    if (!isRedirectError(err)) throw err;
    location = getURLFromRedirectError(err);
  }
  const headers = new Headers();
  appendMutableCookies(headers, store);
  // A large session is split into `…-auth-token.0`, `.1`, … cookies.
  const auth = headers
    .getSetCookie()
    .filter((c) => c.startsWith(`${KEY}=`) || c.startsWith(`${KEY}.`));
  return { message, email, location, auth };
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = `https://${REF}.supabase.co`;
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon";
  process.env.ALLOWED_EMAILS = "owner@example.com";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("signInWithPassword", () => {
  it("allowed: sets the session cookie and goes home, password sent untrimmed", async () => {
    installFetch({ kind: "ok", email: "Owner@Example.com" });
    const { message, location, auth } = await run(form("  owner@example.com ", " pass word "));
    expect(message).toBeNull();
    expect(location).toBe("/");
    expect(auth.some((c) => c.startsWith(`${KEY}=base64-`))).toBe(true);
  });

  for (const logout of ["ok", "500"] as const) {
    it(`not-allowed (logout=${logout}): leaves only a cookie deletion, sent to /login?error=not-allowed`, async () => {
      installFetch({ kind: "ok", email: "evil@example.com" }, logout);
      const { location, auth } = await run(form("evil@example.com", " pass word "));
      expect(location).toBe("/login?error=not-allowed");
      expect(auth.length).toBe(1);
      expect(auth[0]).toMatch(/^sb-abcdefgh-auth-token=;.*Max-Age=0/);
      // Local scope only: a global sign-out would end the account's
      // sessions on every other device too.
      const signOuts = calls.filter((u) => u.includes("/logout"));
      expect(signOuts.length).toBe(1);
      expect(new URL(signOuts[0]).searchParams.get("scope")).toBe("local");
    });
  }

  it("fails closed when ALLOWED_EMAILS is unset", async () => {
    delete process.env.ALLOWED_EMAILS;
    installFetch({ kind: "ok", email: "owner@example.com" });
    const { location, auth } = await run(form("owner@example.com", " pass word "));
    expect(location).toBe("/login?error=not-allowed");
    expect(auth.every((c) => /Max-Age=0/.test(c))).toBe(true);
    const signOuts = calls.filter((u) => u.includes("/logout"));
    expect(signOuts.map((u) => new URL(u).searchParams.get("scope"))).toEqual(["local"]);
  });

  // The allow list judges the account Supabase signed in, never the typed
  // address: an allowed address typed in must not admit another account.
  it("judges the email Supabase returns, not the one typed", async () => {
    installFetch({ kind: "ok", email: "evil@example.com" });
    const { location, auth } = await run(form("owner@example.com", " pass word "));
    expect(location).toBe("/login?error=not-allowed");
    expect(auth.every((c) => /Max-Age=0/.test(c))).toBe(true);
  });

  const failures: [Token & { kind: "fail" }, string][] = [
    [{ kind: "fail", status: 400, code: "invalid_credentials" }, "Wrong email or password."],
    [{ kind: "fail", status: 429, code: "over_request_rate_limit" }, "Too many attempts. Wait a few minutes and try again."],
    [{ kind: "fail", status: 400, code: "email_not_confirmed" }, "Sign-in didn't work. Try again, or use a sign-in link."],
    [{ kind: "fail", status: 500 }, "Sign-in didn't work. Try again, or use a sign-in link."],
    [{ kind: "fail", status: 503 }, "Sign-in didn't work. Try again, or use a sign-in link."],
  ];
  for (const [token, want] of failures) {
    it(`failure ${token.status}/${token.code ?? "none"}: says "${want}", no session, no redirect`, async () => {
      installFetch(token);
      const { message, email, location, auth } = await run(form(" owner@example.com ", "x"));
      expect(message).toBe(want);
      expect(email).toBe("owner@example.com");
      expect(location).toBeNull();
      expect(auth.some((c) => c.includes("base64-"))).toBe(false);
    });
  }

  it("empty fields: asks for both and never calls Supabase", async () => {
    installFetch({ kind: "ok", email: "owner@example.com" });
    for (const [email, password] of [["", "pw"], ["   ", "pw"], ["owner@example.com", ""]]) {
      const { message, location } = await run(form(email, password));
      expect(message).toBe("Enter your email and password.");
      expect(location).toBeNull();
    }
    expect(calls).toEqual([]);
  });
});
