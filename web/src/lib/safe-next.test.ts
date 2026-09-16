import { describe, expect, it } from "vitest";
import { safeNext } from "./safe-next";

const ORIGIN = "https://dash.example";

describe("safeNext", () => {
  it("passes through same-origin relative paths", () => {
    expect(safeNext("/squad", ORIGIN)).toBe("/squad");
    expect(safeNext("/market?expiring=6", ORIGIN)).toBe("/market?expiring=6");
    expect(safeNext("/health#top", ORIGIN)).toBe("/health#top");
  });

  it("rejects every hostile form and falls back to /", () => {
    for (const raw of [
      "//evil.com",
      "https://evil.com",
      "http://evil.com",
      "///evil.com",
      // The URL parser normalises a backslash to a forward slash for
      // http(s) schemes, so these also parse as absolute off-origin URLs
      // despite starting with a single "/".
      "/\\evil.com",
      "/\\/evil.com",
      "/\\evil.com/path",
      // The URL parser strips tab/newline characters outright.
      "/\t/evil.com",
      "/\n/evil.com",
    ]) {
      expect(safeNext(raw, ORIGIN)).toBe("/");
      // The property that actually matters: resolving the guarded value
      // against ORIGIN must never leave ORIGIN. A string-prefix check alone
      // doesn't prove this; resolving it the way the redirect will does.
      expect(new URL(safeNext(raw, ORIGIN), ORIGIN).origin).toBe(ORIGIN);
    }
  });

  it("falls back to / for null, empty, and a bare (non-rooted) path", () => {
    expect(safeNext(null, ORIGIN)).toBe("/");
    expect(safeNext("", ORIGIN)).toBe("/");
    expect(safeNext("squad", ORIGIN)).toBe("/");
  });
});
