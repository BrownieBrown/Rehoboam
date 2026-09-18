import { describe, expect, it } from "vitest";
import { hrefFor } from "./query-href";

describe("hrefFor", () => {
  it("keeps every other param, applies the override, and drops the page", () => {
    expect(hrefFor("/market", { sort: "ask", dir: "asc", page: "3" }, { from: "all" })).toBe(
      "/market?sort=ask&dir=asc&from=all",
    );
  });

  it("clears a param with null and returns the bare path when nothing is left", () => {
    expect(hrefFor("/market", { from: "all" }, { from: null })).toBe("/market");
    expect(hrefFor("/", { position: "GK", owner: "mine" }, { position: null })).toBe("/?owner=mine");
  });

  it("skips empty values", () => {
    expect(hrefFor("/", { q: "", club: undefined }, {})).toBe("/");
  });
});
