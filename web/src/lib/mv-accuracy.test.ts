import { describe, expect, it } from "vitest";
import { accuracySentence, directionRight, summarize } from "./mv-accuracy";

const row = (o: Partial<Parameters<typeof summarize>[0][number]>) => ({
  scored: 0,
  directional: 0,
  direction_hits: 0,
  mae_pct: null,
  baseline_mae_pct: null,
  ...o,
});

describe("summarize", () => {
  it("weights each update by how many forecasts it scored", () => {
    const s = summarize([
      row({ scored: 300, directional: 200, direction_hits: 190, mae_pct: 0.5, baseline_mae_pct: 2 }),
      row({ scored: 100, directional: 50, direction_hits: 40, mae_pct: 1.3, baseline_mae_pct: 2.4 }),
    ]);
    expect(s).toEqual({ updates: 2, scored: 400, maePct: 0.7, baselineMaePct: 2.1, directionRate: 0.92 });
  });

  it("skips updates with nothing scored", () => {
    const s = summarize([row({ scored: 0 }), row({ scored: 10, mae_pct: 1, baseline_mae_pct: 1 })]);
    expect(s.updates).toBe(1);
    expect(s.directionRate).toBeNull();
  });

  it("is empty when nothing is scored", () => {
    expect(summarize([])).toEqual({ updates: 0, scored: 0, maePct: null, baselineMaePct: null, directionRate: null });
  });
});

describe("accuracySentence", () => {
  it("says nothing is scored yet, and when scores arrive", () => {
    expect(accuracySentence(summarize([]))).toBe(
      "No forecast has been scored yet. Each forecast is scored by the first data run after its update, usually the next morning.",
    );
  });

  it("says the forecast beats no change only when its miss is smaller", () => {
    const s = { updates: 14, scored: 7000, maePct: 0.7, baselineMaePct: 2.1, directionRate: 0.953 };
    expect(accuracySentence(s)).toBe(
      'Over the last 14 updates (7000 forecasts), the forecast missed by 0.70 points of percent on average, better than "no change" at 2.10. It called the direction right 95% of the time when both it and the update moved.',
    );
  });

  it("says so when it ties or loses", () => {
    const tie = { updates: 1, scored: 1, maePct: 1, baselineMaePct: 1, directionRate: null };
    expect(accuracySentence(tie)).toBe(
      'Over the last update (1 forecast), the forecast missed by 1.00 points of percent on average, the same as "no change" at 1.00.',
    );
    const loss = { updates: 2, scored: 5, maePct: 3, baselineMaePct: 1, directionRate: 0 };
    expect(accuracySentence(loss)).toBe(
      'Over the last 2 updates (5 forecasts), the forecast missed by 3.00 points of percent on average, worse than "no change" at 1.00. It called the direction right 0% of the time when both it and the update moved.',
    );
  });
});

describe("directionRight", () => {
  it("is a share of the directional forecasts, or a dash", () => {
    expect(directionRight({ directional: 200, direction_hits: 190 })).toBe("95% of 200");
    expect(directionRight({ directional: 0, direction_hits: 0 })).toBe("—");
  });
});
