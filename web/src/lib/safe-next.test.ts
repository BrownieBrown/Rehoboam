import { describe, expect, it } from "vitest";
import { safeNext } from "./safe-next";

const ORIGIN = "https://dash.example";

// The property that actually matters, checked the way the callback really
// uses the value: it does its own `new URL(next, origin)`. Simulating that
// second parse here is what would have caught round 3's miss — a bare
// same-origin *path* can still be protocol-relative under a second parse;
// only an already-absolute href is safe under re-parsing.
function staysOnOrigin(next: string): boolean {
  return new URL(next, ORIGIN).origin === ORIGIN;
}

describe("safeNext", () => {
  it("resolves same-origin relative paths to an absolute URL", () => {
    expect(safeNext("/squad", ORIGIN)).toBe(`${ORIGIN}/squad`);
    expect(safeNext("/market?expiring=6", ORIGIN)).toBe(`${ORIGIN}/market?expiring=6`);
    expect(safeNext("/health#top", ORIGIN)).toBe(`${ORIGIN}/health#top`);
  });

  it("rejects every off-origin form and falls back to the origin root", () => {
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
      expect(safeNext(raw, ORIGIN)).toBe(`${ORIGIN}/`);
      expect(staysOnOrigin(safeNext(raw, ORIGIN))).toBe(true);
    }
  });

  it("resolves dot-segments same-origin without leaving a re-parseable protocol-relative path", () => {
    // A single resolve collapses the ".." segment, leaving a same-origin URL
    // whose *path* is "//evil.com" — protocol-relative on a second parse,
    // which is exactly what the callback does. `url.origin` legitimately
    // equals ORIGIN here (this is not a cross-origin input, unlike the
    // group above), so safeNext does not fall back; the fix is that it
    // returns the fully resolved href instead of a bare path, so there is
    // nothing left to re-interpret on that second parse.
    for (const raw of [
      "/..//evil.com",
      "/../..//evil.com",
      "/./..//evil.com",
      "/a/../..//evil.com",
    ]) {
      const result = safeNext(raw, ORIGIN);
      expect(result.startsWith(ORIGIN)).toBe(true);
      expect(staysOnOrigin(result)).toBe(true);
    }
  });

  it("falls back to the origin root for null, empty, and a bare (non-rooted) path", () => {
    expect(safeNext(null, ORIGIN)).toBe(`${ORIGIN}/`);
    expect(safeNext("", ORIGIN)).toBe(`${ORIGIN}/`);
    expect(safeNext("squad", ORIGIN)).toBe(`${ORIGIN}/`);
  });
});
