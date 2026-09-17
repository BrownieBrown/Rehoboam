import { describe, expect, it } from "vitest";
import { bySeller, sellerScope } from "./market-filter";

const row = (id: string, seller: string) => ({ id, seller });
const rows = [row("k1", "Kickbase"), row("m1", "Anna"), row("k2", "Kickbase"), row("us", "Marco")];

describe("sellerScope", () => {
  it("defaults to Kickbase when the param is absent or unknown", () => {
    for (const raw of [undefined, "", "kickbase", "ALL", "Managers", "x", "__proto__"]) {
      expect(sellerScope(raw)).toBe("kickbase");
    }
  });

  it("reads the two other scopes exactly", () => {
    expect(sellerScope("managers")).toBe("managers");
    expect(sellerScope("all")).toBe("all");
  });
});

describe("bySeller", () => {
  it("keeps only Kickbase's own listings by default, in the given order", () => {
    expect(bySeller(rows, "kickbase").map((r) => r.id)).toEqual(["k1", "k2"]);
  });

  it("keeps every manager's listing, ours included, for managers", () => {
    expect(bySeller(rows, "managers").map((r) => r.id)).toEqual(["m1", "us"]);
  });

  it("keeps everything for all, and never errors on an empty list", () => {
    expect(bySeller(rows, "all")).toEqual(rows);
    expect(bySeller([], "kickbase")).toEqual([]);
  });

  it("matches the seller name exactly", () => {
    expect(bySeller([row("x", "kickbase"), row("y", "Kickbase ")], "kickbase")).toEqual([]);
  });
});
