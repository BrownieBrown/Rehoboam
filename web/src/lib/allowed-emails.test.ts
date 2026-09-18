import { describe, expect, it } from "vitest";
import { isAllowedEmail, parseAllowList } from "./allowed-emails";

// A Cyrillic "е" (U+0435, CYRILLIC SMALL LETTER IE) standing in for the
// Latin "e" in "example" — visually indistinguishable, never equal.
const CYRILLIC_E = "е";

describe("parseAllowList", () => {
  it("fails closed: unset, empty, or comma/space-only input parses to an empty set", () => {
    expect(parseAllowList(undefined)).toEqual(new Set());
    expect(parseAllowList("")).toEqual(new Set());
    expect(parseAllowList("   ")).toEqual(new Set());
    expect(parseAllowList(",,, ,")).toEqual(new Set());
  });

  it("splits on commas, trims, lower-cases, and drops empty entries", () => {
    expect(parseAllowList("a@x.com, , B@y.com,")).toEqual(new Set(["a@x.com", "b@y.com"]));
    expect(parseAllowList("  Marco@Example.COM  ")).toEqual(new Set(["marco@example.com"]));
  });
});

describe("isAllowedEmail", () => {
  const LIST = "marco@example.com";

  it("fails closed for every unset/empty/comma-only list, even for the right address", () => {
    for (const raw of [undefined, "", "   ", ",,, ,"]) {
      expect(isAllowedEmail("marco@example.com", raw)).toBe(false);
    }
  });

  it("allows an exact match after trimming and lower-casing both sides", () => {
    expect(isAllowedEmail("marco@example.com", LIST)).toBe(true);
    expect(isAllowedEmail("  Marco@Example.COM  ", LIST)).toBe(true);
  });

  it("refuses every non-exact form: substring, prefix/suffix, near-miss TLD, homoglyph, embedded", () => {
    expect(isAllowedEmail("evilmarco@example.com", LIST)).toBe(false);
    expect(isAllowedEmail("marco@example.com.evil.net", LIST)).toBe(false);
    expect(isAllowedEmail("marco@example.co", LIST)).toBe(false);
    expect(isAllowedEmail(`marco@exampl${CYRILLIC_E}.com`, LIST)).toBe(false);
    expect(isAllowedEmail("please contact marco@example.com now", LIST)).toBe(false);
  });

  it("refuses empty, null, and undefined addresses", () => {
    expect(isAllowedEmail("", LIST)).toBe(false);
    expect(isAllowedEmail(null, LIST)).toBe(false);
    expect(isAllowedEmail(undefined, LIST)).toBe(false);
  });

  it("matches against a multi-address list, and its empty entries allow nothing", () => {
    const raw = "a@x.com, , B@y.com,";
    expect(isAllowedEmail("a@x.com", raw)).toBe(true);
    expect(isAllowedEmail("b@y.com", raw)).toBe(true);
    expect(isAllowedEmail("B@Y.COM", raw)).toBe(true);
    expect(isAllowedEmail("", raw)).toBe(false);
    expect(isAllowedEmail("   ", raw)).toBe(false);
    expect(isAllowedEmail("c@z.com", raw)).toBe(false);
  });
});
