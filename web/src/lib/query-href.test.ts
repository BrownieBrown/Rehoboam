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

  it("keeps the page when the override opens a player", () => {
    expect(hrefFor("/", { position: "GK", page: "2" }, { player: "123" })).toBe(
      "/?position=GK&page=2&player=123",
    );
  });

  it("still drops the page for every other override, including closing a player", () => {
    expect(hrefFor("/", { position: "GK", page: "2", player: "123" }, { player: null })).toBe(
      "/?position=GK",
    );
    expect(hrefFor("/market", { sort: "ask", page: "2" }, { sort: "market_value" })).toBe(
      "/market?sort=market_value",
    );
  });
});
