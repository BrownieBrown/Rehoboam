import { describe, expect, it } from "vitest";
import { sparkline, type Point } from "./sparkline";

describe("sparkline", () => {
  it("draws a straight path from 0,* to width,* for two points", () => {
    const out = sparkline(
      [
        { day: "2026-01-01", market_value: 10 },
        { day: "2026-01-02", market_value: 20 },
      ],
      100,
      50,
    );
    expect(out).not.toBeNull();
    const [start, end] = out!.path.split(" ");
    expect(start.startsWith("0,")).toBe(true);
    expect(end.startsWith("100,")).toBe(true);
  });

  it("puts the lowest value at the bottom and the highest at the top of a rising series", () => {
    const out = sparkline(
      [
        { day: "2026-01-01", market_value: 10 },
        { day: "2026-01-02", market_value: 20 },
        { day: "2026-01-03", market_value: 30 },
      ],
      100,
      50,
    );
    expect(out).not.toBeNull();
    const coords = out!.path.split(" ").map((pair) => pair.split(",").map(Number));
    expect(coords[0][1]).toBe(50); // lowest value -> y = height
    expect(coords[2][1]).toBe(0); // highest value -> y = 0
  });

  it("returns the first and last value alongside the path", () => {
    const points: Point[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 15 },
      { day: "2026-01-03", market_value: 20 },
    ];
    const out = sparkline(points, 100, 50);
    expect(out!.first).toEqual(points[0]);
    expect(out!.last).toEqual(points[2]);
  });

  it("returns null with fewer than two points", () => {
    expect(sparkline([], 100, 50)).toBeNull();
    expect(sparkline([{ day: "2026-01-01", market_value: 10 }], 100, 50)).toBeNull();
  });

  it("returns null when every value is identical -- a flat line says nothing", () => {
    const flat: Point[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 10 },
      { day: "2026-01-03", market_value: 10 },
    ];
    expect(sparkline(flat, 100, 50)).toBeNull();
  });

  it("rounds coordinates to one decimal", () => {
    const out = sparkline(
      [
        { day: "2026-01-01", market_value: 10 },
        { day: "2026-01-02", market_value: 15 },
        { day: "2026-01-03", market_value: 20 },
      ],
      100,
      50,
    );
    // x for the middle point: (1/2) * 100 = 50 exactly, but a non-round
    // width/height combination must not carry more than one decimal digit.
    for (const pair of out!.path.split(" ")) {
      for (const n of pair.split(",")) {
        const decimals = n.includes(".") ? n.split(".")[1].length : 0;
        expect(decimals).toBeLessThanOrEqual(1);
      }
    }
  });

  it("does not mutate its input", () => {
    const points: Point[] = [
      { day: "2026-01-01", market_value: 10 },
      { day: "2026-01-02", market_value: 20 },
    ];
    const copy = JSON.parse(JSON.stringify(points));
    sparkline(points, 100, 50);
    expect(points).toEqual(copy);
  });
});
