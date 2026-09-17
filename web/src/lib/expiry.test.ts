import { describe, expect, it } from "vitest";
import { expiringWithin, listingsLine } from "./expiry";

const NOW = 1_800_000_000;
const row = (id: string, expires_at: number | null) => ({ id, expires_at });

describe("expiringWithin", () => {
  const rows = [
    row("soon", NOW + 3600),
    row("edge", NOW + 6 * 3600),
    row("later", NOW + 6 * 3600 + 1),
    row("manager", null),
    row("past", NOW - 60),
  ];

  it("keeps listings due within the window, the edge included, in the given order", () => {
    expect(expiringWithin(rows, 6, NOW).map((r) => r.id)).toEqual(["soon", "edge", "past"]);
  });

  it("never keeps a listing that has no expiry", () => {
    expect(expiringWithin([row("manager", null)], 1000, NOW)).toEqual([]);
  });

  it("returns nothing, not an error, when nothing is due", () => {
    expect(expiringWithin([row("later", NOW + 7 * 3600)], 6, NOW)).toEqual([]);
    expect(expiringWithin([], 6, NOW)).toEqual([]);
  });
});

describe("listingsLine", () => {
  it("counts the whole snapshot when unfiltered", () => {
    expect(listingsLine(48, 48)).toBe("48 listings");
    expect(listingsLine(1, 1)).toBe("1 listing");
    expect(listingsLine(0, 0)).toBe("0 listings");
  });

  it("says what the filter kept out of what the snapshot holds", () => {
    expect(listingsLine(3, 48, 6)).toBe("3 of 48 listings expiring under 6 h");
    expect(listingsLine(0, 48, 6)).toBe("0 of 48 listings expiring under 6 h");
    expect(listingsLine(0, 1, 6)).toBe("0 of 1 listing expiring under 6 h");
  });
});
