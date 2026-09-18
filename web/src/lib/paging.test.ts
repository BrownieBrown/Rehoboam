import { describe, expect, it } from "vitest";
import {
  clampPage,
  MAX_PAGE,
  PAGE_SIZE,
  pageHref,
  pageOffset,
  pageSummary,
  parsePage,
} from "./paging";

describe("parsePage", () => {
  it("passes a plain positive integer through", () => {
    expect(parsePage("1")).toBe(1);
    expect(parsePage("2")).toBe(2);
    expect(parsePage("37")).toBe(37);
    expect(parsePage(String(MAX_PAGE))).toBe(MAX_PAGE);
  });

  it("caps a page past MAX_PAGE, however large", () => {
    expect(parsePage(String(MAX_PAGE + 1))).toBe(MAX_PAGE);
    expect(parsePage("9007199254740993")).toBe(MAX_PAGE); // past 2^53
    expect(parsePage("9".repeat(400))).toBe(MAX_PAGE); // Number() gives Infinity
  });

  // The page becomes an OFFSET. Every one of these must land on page 1 - an
  // integer, never NaN, never negative, never zero, never a fraction.
  it("falls back to page 1 for everything that is not a plain positive integer", () => {
    const hostile: unknown[] = [
      undefined,
      null,
      "",
      " ",
      "0",
      "00",
      "01",
      "-1",
      "+1",
      "-0",
      " 1",
      "1 ",
      "\t2",
      "2\n",
      "1.0",
      "1.5",
      "1e3",
      "1E3",
      "0x10",
      "0b1",
      "0o7",
      "Infinity",
      "-Infinity",
      "NaN",
      "1,000",
      "1_000",
      "1/0",
      "2;drop table rehoboam.web_players",
      "2 offset 0",
      "2--",
      "１", // fullwidth digit one
      "٣", // Arabic-Indic digit three
      "2\u0000", // embedded null byte
      "__proto__",
      "constructor",
      ["2", "3"], // a repeated ?page=2&page=3
      2, // not a string
      {},
    ];
    for (const raw of hostile) {
      const page = parsePage(raw);
      expect(page, JSON.stringify(raw) ?? String(raw)).toBe(1);
    }
  });

  it("always returns an integer in [1, MAX_PAGE]", () => {
    for (const raw of ["1", "999", "1000", "1001", "123456789012345678901234567890", "abc"]) {
      const page = parsePage(raw);
      expect(Number.isInteger(page)).toBe(true);
      expect(page).toBeGreaterThanOrEqual(1);
      expect(page).toBeLessThanOrEqual(MAX_PAGE);
    }
  });
});

describe("clampPage", () => {
  it("keeps a page that has rows", () => {
    expect(clampPage(1, 213)).toBe(1);
    expect(clampPage(5, 213)).toBe(5);
  });

  it("holds a page past the end on the last page that has rows", () => {
    expect(clampPage(6, 213)).toBe(5);
    expect(clampPage(MAX_PAGE, 213)).toBe(5);
    expect(clampPage(2, PAGE_SIZE)).toBe(1); // exactly one full page
    expect(clampPage(3, PAGE_SIZE + 1)).toBe(2);
  });

  it("is page 1 when nothing matches, and never below 1", () => {
    expect(clampPage(4, 0)).toBe(1);
    expect(clampPage(0, 213)).toBe(1);
    expect(clampPage(-3, 213)).toBe(1);
  });
});

describe("pageOffset", () => {
  it("skips the rows of the pages before", () => {
    expect(pageOffset(1)).toBe(0);
    expect(pageOffset(2)).toBe(PAGE_SIZE);
    expect(pageOffset(MAX_PAGE)).toBe((MAX_PAGE - 1) * PAGE_SIZE);
  });
});

describe("pageSummary", () => {
  it("names the rows shown and the total the filters match", () => {
    expect(pageSummary(0, 50, 213)).toBe("1–50 of 213");
    expect(pageSummary(200, 13, 213)).toBe("201–213 of 213");
    expect(pageSummary(0, 7, 7)).toBe("1–7 of 7");
  });

  it("never claims a row it does not show", () => {
    expect(pageSummary(0, 0, 0)).toBe("0 of 0");
    expect(pageSummary(50, 0, 12)).toBe("0 of 12");
  });
});

describe("pageHref", () => {
  it("keeps every other parameter and sets the page", () => {
    expect(
      pageHref("/", { position: "Defender", owner: "free", sort: "points", dir: "asc" }, 3),
    ).toBe("/?position=Defender&owner=free&sort=points&dir=asc&page=3");
  });

  it("replaces an existing page instead of repeating it", () => {
    expect(pageHref("/", { page: "2", q: "müller" }, 3)).toBe("/?q=m%C3%BCller&page=3");
  });

  it("drops the parameter for page 1, and empty or absent values", () => {
    expect(pageHref("/", { page: "4", club: "", q: undefined }, 1)).toBe("/");
    expect(pageHref("/", { club: "Bayern", page: "4" }, 1)).toBe("/?club=Bayern");
  });

  it("encodes values rather than letting them break out of the query", () => {
    expect(pageHref("/", { q: "a&page=9#x" }, 2)).toBe("/?q=a%26page%3D9%23x&page=2");
  });
});
