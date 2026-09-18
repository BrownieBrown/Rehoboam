import { describe, expect, it } from "vitest";
import { changes } from "./mv-changes";
import type { ChartPoint } from "./chart";

describe("changes", () => {
  it("returns empty for fewer than two points", () => {
    expect(changes([], 10)).toEqual([]);
    expect(changes([{ day: "2026-01-01", market_value: 10 }], 10)).toEqual([]);
  });

  it("orders consecutive differences newest first", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 12 },
      { day: "2026-01-03", market_value: 15 },
    ];
    expect(changes(points, 10)).toEqual([
      { day: "2026-01-03", change: 3 },
      { day: "2026-01-02", change: 2 },
    ]);
  });

  it("drops days where the value did not move", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 10 },
      { day: "2026-01-03", market_value: 15 },
      { day: "2026-01-04", market_value: 15 },
    ];
    expect(changes(points, 10)).toEqual([{ day: "2026-01-03", change: 5 }]);
  });

  it("caps the result at limit even when more moves exist", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 11 },
      { day: "2026-01-03", market_value: 12 },
      { day: "2026-01-04", market_value: 13 },
    ];
    expect(changes(points, 2)).toEqual([
      { day: "2026-01-04", change: 1 },
      { day: "2026-01-03", change: 1 },
    ]);
  });

  it("signs a fall as negative", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 20 },
      { day: "2026-01-02", market_value: 15 },
    ];
    expect(changes(points, 10)).toEqual([{ day: "2026-01-02", change: -5 }]);
  });
});
