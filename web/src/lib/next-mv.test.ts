import { describe, expect, it } from "vitest";
import { NEXT_MV_HINT, nextMv, noForecastNote } from "./next-mv";

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

describe("noForecastNote", () => {
  const NOTE =
    "No market-value forecast is live right now. Each data run (about 07:00 and 19:00 Berlin) writes the forecast for that evening's update, and it stops being shown at 22:00, when Kickbase moves the values.";

  it("explains an empty column when no row has a forecast", () => {
    expect(noForecastNote([{ next_mv_pct: null }, { next_mv_pct: null }])).toBe(NOTE);
  });

  it("says nothing when at least one row has a forecast", () => {
    expect(noForecastNote([{ next_mv_pct: null }, { next_mv_pct: 1.5 }])).toBeNull();
    expect(noForecastNote([{ next_mv_pct: 0 }])).toBeNull();
  });

  it("says nothing when there are no rows at all", () => {
    expect(noForecastNote([])).toBeNull();
  });
});
