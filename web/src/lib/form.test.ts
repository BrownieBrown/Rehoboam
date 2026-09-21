import { describe, expect, it } from "vitest";
import { formEntries } from "./form";

const m = (day_number: number, status: number | null, points: number | null) => ({
  season: "2026/2027",
  day_number,
  match_date: null,
  points,
  minutes: null,
  status,
  is_home: null,
  opponent: null,
  match_at: null,
});

describe("formEntries", () => {
  it("returns the newest matchdays oldest-first", () => {
    const out = formEntries([m(3, 5, 253), m(2, 5, 272), m(1, 5, 234)], 3);
    expect(out.map((e) => e.day_number)).toEqual([1, 2, 3]);
  });

  it("separates a start from a substitute appearance", () => {
    expect(formEntries([m(1, 5, 90)], 1)[0].role).toBe("started");
    expect(formEntries([m(1, 3, 40)], 1)[0].role).toBe("came on");
  });

  it("does not report points for a matchday he was not on the pitch", () => {
    for (const status of [4, 1, 0, null]) {
      const [entry] = formEntries([m(1, status, 0)], 1);
      expect(entry.role).toBe("did not play");
      expect(entry.points).toBeNull();
    }
  });

  it("pads to `count` so the strip keeps its shape for a new player", () => {
    expect(formEntries([m(1, 5, 120)], 5)).toHaveLength(5);
  });

  it("pads on the right, after his real matches, not in front of them", () => {
    const out = formEntries([m(2, 5, 200), m(1, 5, 100)], 5);
    expect(out.map((e) => e.day_number)).toEqual([1, 2, 0, 0, 0]);
    expect(out.slice(2).every((e) => e.role === "did not play" && e.points === null)).toBe(true);
  });

  it("never mutates its input", () => {
    const input = [m(1, 5, 10), m(2, 5, 20)];
    const copy = JSON.parse(JSON.stringify(input));
    formEntries(input, 2);
    expect(input).toEqual(copy);
  });
});
