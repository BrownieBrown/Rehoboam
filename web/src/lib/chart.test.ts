import { describe, expect, it } from "vitest";
import { chart, type ChartPoint } from "./chart";

/** "M x,y L x,y L x,y" -> [[x,y],[x,y],[x,y]] */
function coords(path: string): number[][] {
  return path
    .replace(/^M\s*/, "")
    .split(" L ")
    .map((pair) => pair.trim().split(",").map(Number));
}

describe("chart", () => {
  it("returns null for fewer than two points", () => {
    expect(chart([], 100, 50)).toBeNull();
    expect(chart([{ day: "2026-01-01", market_value: 10 }], 100, 50)).toBeNull();
  });

  it("draws a straight line from x=0 to x=width for two points with no padding", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 20 },
    ];
    const out = chart(points, 100, 50, 0);
    expect(out).not.toBeNull();
    const cs = coords(out!.line);
    expect(cs[0][0]).toBe(0);
    expect(cs[1][0]).toBe(100);
    expect(out!.first).toEqual(points[0]);
    expect(out!.last).toEqual(points[1]);
  });

  it("puts the lowest value at the bottom and the highest at the top of a rising series", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 20 },
      { day: "2026-01-03", market_value: 30 },
    ];
    const out = chart(points, 100, 50, 0);
    expect(out).not.toBeNull();
    const cs = coords(out!.line);
    expect(cs[0][1]).toBe(50); // lowest value -> y = height
    expect(cs[2][1]).toBe(0); // highest value -> y = 0
    expect(out!.low.point).toEqual(points[0]);
    expect(out!.high.point).toEqual(points[2]);
    expect(out!.low.y).toBe(50);
    expect(out!.high.y).toBe(0);
  });

  it("is not null for a flat series -- draws a straight line through the middle, high/low still set", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 10 },
      { day: "2026-01-03", market_value: 10 },
    ];
    const out = chart(points, 100, 50);
    expect(out).not.toBeNull();
    const cs = coords(out!.line);
    const ys = new Set(cs.map((c) => c[1]));
    expect(ys.size).toBe(1); // every point on the same horizontal line
    expect(out!.high.point.market_value).toBe(10);
    expect(out!.low.point.market_value).toBe(10);
  });

  it("spaces x by date, not by index -- a gap in the series shows as a gap in slope", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 0 },
      { day: "2026-01-02", market_value: 0 }, // 1 day after the first
      { day: "2026-01-10", market_value: 0 }, // 9 days after the first, not the midpoint
    ];
    const out = chart(points, 90, 50, 0);
    const cs = coords(out!.line);
    // total span is 9 days; the middle point sits 1/9 of the way across,
    // not at index-based 1/2 (which would place it at x=45).
    expect(cs[1][0]).toBeCloseTo((1 / 9) * 90, 1);
  });

  it("pads the y axis so the extreme dots are not clipped", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 20 },
    ];
    const out = chart(points, 100, 50, 6);
    const cs = coords(out!.line);
    for (const [, y] of cs) {
      expect(y).toBeGreaterThanOrEqual(6);
      expect(y).toBeLessThanOrEqual(44);
    }
  });

  it("rounds coordinates to one decimal", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 15 },
      { day: "2026-01-03", market_value: 20 },
    ];
    const out = chart(points, 100, 50);
    for (const [x, y] of coords(out!.line)) {
      for (const n of [x, y]) {
        const s = String(n);
        const decimals = s.includes(".") ? s.split(".")[1].length : 0;
        expect(decimals).toBeLessThanOrEqual(1);
      }
    }
  });

  it("does not mutate its input", () => {
    const points: ChartPoint[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 20 },
    ];
    const copy = JSON.parse(JSON.stringify(points));
    chart(points, 100, 50);
    expect(points).toEqual(copy);
  });

  it("insets x as well as y so the extreme dots are not clipped", () => {
    const c = chart(
      [
        { day: "2026-01-01", market_value: 1 },
        { day: "2026-01-11", market_value: 9 },
      ],
      100,
      50,
    )!;
    expect(c.low.x).toBeCloseTo(6, 1);
    expect(c.high.x).toBeCloseTo(94, 1);
    expect(c.low.y).toBeCloseTo(44, 1);
    expect(c.high.y).toBeCloseTo(6, 1);
  });

  it("closes the area path along the bottom of the box", () => {
    const c = chart(
      [
        { day: "2026-01-01", market_value: 1 },
        { day: "2026-01-11", market_value: 9 },
      ],
      100,
      50,
    )!;
    expect(c.area.startsWith("M")).toBe(true);
    expect(c.area.endsWith("Z")).toBe(true);
    expect(c.area).toContain(c.line.slice(1));
    expect(c.area).toContain("50");
  });

  it("draws a flat series through the middle, not along an edge", () => {
    const c = chart(
      [
        { day: "2026-01-01", market_value: 7 },
        { day: "2026-01-05", market_value: 7 },
      ],
      100,
      50,
    )!;
    const ys = [...c.line.matchAll(/,([\d.]+)/g)].map((m) => Number(m[1]));
    expect(new Set(ys).size).toBe(1);
    expect(ys[0]).toBeCloseTo(25, 1);
  });
});
