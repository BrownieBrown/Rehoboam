import { describe, expect, it } from "vitest";
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
