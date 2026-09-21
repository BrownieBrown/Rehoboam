import { describe, expect, it } from "vitest";
import { fairVerdict } from "./fair-value";

describe("fairVerdict", () => {
  it("says nothing without a fair price", () => {
    expect(fairVerdict(null, 10_000_000)).toBeNull();
    expect(fairVerdict(undefined, 10_000_000)).toBeNull();
  });

  it("says nothing without a usable market value", () => {
    expect(fairVerdict(10_000_000, null)).toBeNull();
    expect(fairVerdict(10_000_000, 0)).toBeNull();
  });

  it("measures the gap against market value, positive when he is cheap", () => {
    expect(fairVerdict(12_500_000, 10_000_000)?.gapPct).toBeCloseTo(25);
    expect(fairVerdict(7_000_000, 10_000_000)?.gapPct).toBeCloseTo(-30);
  });

  it("calls an exact match fair", () => {
    expect(fairVerdict(10_000_000, 10_000_000)).toMatchObject({ label: "fair", tone: "neutral" });
  });
});
