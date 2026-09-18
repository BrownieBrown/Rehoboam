import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isPublicPath } from "./auth-paths";

describe("isPublicPath", () => {
  it("lets the login page and the auth callback through", () => {
    expect(isPublicPath("/login")).toBe(true);
    expect(isPublicPath("/auth/callback")).toBe(true);
    expect(isPublicPath("/auth/callback?code=abc")).toBe(true);
  });

  it("gates every data page", () => {
    for (const path of ["/", "/squad", "/market", "/health", "/loginx", "/auth"]) {
      expect(isPublicPath(path)).toBe(false);
    }
  });
});

// requireSession() itself: `./supabase` is mocked so no real Supabase
// client is built, and `next/navigation`'s `redirect` is mocked to throw —
// matching its real behaviour of aborting the render — so each not-allowed
// or anonymous case can be asserted without ever reaching return-the-user.
const mockGetUser = vi.fn();
vi.mock("./supabase", () => ({
  createServerClient: vi.fn(async () => ({ auth: { getUser: mockGetUser } })),
}));

class RedirectSignal extends Error {
  constructor(public to: string) {
    super(`NEXT_REDIRECT:${to}`);
  }
}
const mockRedirect = vi.fn((to: string) => {
  throw new RedirectSignal(to);
});
vi.mock("next/navigation", () => ({ redirect: mockRedirect }));

const { requireSession } = await import("./auth");

describe("requireSession", () => {
  beforeEach(() => {
    mockGetUser.mockReset();
    mockRedirect.mockClear();
    delete process.env.ALLOWED_EMAILS;
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("redirects to /login when there is no user", async () => {
    mockGetUser.mockResolvedValue({ data: { user: null } });
    await expect(requireSession()).rejects.toBeInstanceOf(RedirectSignal);
    expect(mockRedirect).toHaveBeenCalledTimes(1);
    expect(mockRedirect).toHaveBeenCalledWith("/login");
  });

  it("redirects to /login?error=not-allowed for a signed-in but not-allowed user", async () => {
    process.env.ALLOWED_EMAILS = "owner@example.com";
    mockGetUser.mockResolvedValue({ data: { user: { email: "evil@example.com" } } });
    await expect(requireSession()).rejects.toBeInstanceOf(RedirectSignal);
    expect(mockRedirect).toHaveBeenCalledTimes(1);
    expect(mockRedirect).toHaveBeenCalledWith("/login?error=not-allowed");
  });

  it("fails closed: ALLOWED_EMAILS unset redirects even the would-be owner", async () => {
    mockGetUser.mockResolvedValue({ data: { user: { email: "owner@example.com" } } });
    await expect(requireSession()).rejects.toBeInstanceOf(RedirectSignal);
    expect(mockRedirect).toHaveBeenCalledTimes(1);
    expect(mockRedirect).toHaveBeenCalledWith("/login?error=not-allowed");
  });

  it("returns the user when allowed, and never redirects", async () => {
    process.env.ALLOWED_EMAILS = "owner@example.com";
    const user = { email: "Owner@Example.com" };
    mockGetUser.mockResolvedValue({ data: { user } });
    await expect(requireSession()).resolves.toBe(user);
    expect(mockRedirect).not.toHaveBeenCalled();
  });
});
