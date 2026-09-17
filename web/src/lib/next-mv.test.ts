import { describe, expect, it } from "vitest";
import { NEXT_MV_HINT, nextMv } from "./next-mv";

describe("nextMv", () => {
  it("formats a rise with its euro change", () => {
    expect(nextMv(2.1, 820_000)).toEqual({ pct: "+2.10%", change: "+820,000", tone: "positive" });
  });

  it("formats a fall with a real minus sign", () => {
    expect(nextMv(-0.45, -45_000)).toEqual({ pct: "−0.45%", change: "−45,000", tone: "negative" });
  });

  it("takes its tone from the euro change when the percent rounds to zero", () => {
    expect(nextMv(0, 1_234)).toEqual({ pct: "0.00%", change: "+1,234", tone: "positive" });
    expect(nextMv(0, 0)).toEqual({ pct: "0.00%", change: "0", tone: "neutral" });
  });

  it("returns null when either half is missing", () => {
    expect(nextMv(null, 5)).toBeNull();
    expect(nextMv(1, null)).toBeNull();
    expect(nextMv(null, null)).toBeNull();
  });

  it("names the update the forecast is for", () => {
    expect(NEXT_MV_HINT).toBe("Forecast for tonight's ~22:00 market-value update");
  });
});
