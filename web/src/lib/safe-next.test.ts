import { describe, expect, it } from "vitest";
import { safeNext } from "./safe-next";

describe("safeNext", () => {
  it("passes through same-origin relative paths", () => {
    expect(safeNext("/squad")).toBe("/squad");
    expect(safeNext("/market?expiring=6")).toBe("/market?expiring=6");
  });

  it("rejects every hostile form and falls back to /", () => {
    for (const raw of ["//evil.com", "https://evil.com", "http://evil.com", "///evil.com"]) {
      expect(safeNext(raw)).toBe("/");
    }
  });

  it("falls back to / for null, empty, and a bare (non-rooted) path", () => {
    expect(safeNext(null)).toBe("/");
    expect(safeNext("")).toBe("/");
    expect(safeNext("squad")).toBe("/");
  });
});
