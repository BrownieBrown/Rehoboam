import { describe, expect, it } from "vitest";
import { RANGES, rangeDays, rangeLabel } from "./mv-range";

describe("RANGES", () => {
  it("lists the five ranges the panel offers, shortest first", () => {
    expect(RANGES).toEqual(["7d", "1m", "3m", "6m", "1y"]);
  });
});

describe("rangeDays", () => {
  it("maps every range to its day count", () => {
    expect(rangeDays("7d")).toBe(7);
    expect(rangeDays("1m")).toBe(30);
    expect(rangeDays("3m")).toBe(90);
    expect(rangeDays("6m")).toBe(180);
    expect(rangeDays("1y")).toBe(365);
  });

  it("defaults to 3m for anything unknown", () => {
    expect(rangeDays(undefined)).toBe(90);
    expect(rangeDays("")).toBe(90);
    expect(rangeDays("2y")).toBe(90);
  });

  it("defaults to 3m for a prototype-pollution attempt rather than resolving it off Object.prototype", () => {
    expect(rangeDays("__proto__")).toBe(90);
    expect(rangeDays("constructor")).toBe(90);
    expect(rangeDays("toString")).toBe(90);
  });
});

describe("rangeLabel", () => {
  it("names every range in words", () => {
    expect(rangeLabel("7d")).toBe("7 days");
    expect(rangeLabel("1m")).toBe("1 month");
    expect(rangeLabel("3m")).toBe("3 months");
    expect(rangeLabel("6m")).toBe("6 months");
    expect(rangeLabel("1y")).toBe("1 year");
  });

  it("defaults to 3 months for anything unknown", () => {
    expect(rangeLabel(undefined)).toBe("3 months");
    expect(rangeLabel("__proto__")).toBe("3 months");
  });
});
