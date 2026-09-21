import { describe, expect, it } from "vitest";
import { availability } from "./availability";

describe("availability", () => {
  it("calls 0 fit, in a positive tone", () => {
    expect(availability(0)).toEqual({ label: "Fit", tone: "positive" });
  });

  it("calls the codes this codebase knows are out unavailable", () => {
    expect(availability(4)).toEqual({ label: "Out", tone: "negative" });
    expect(availability(256)).toEqual({ label: "Out", tone: "negative" });
  });

  it("names any other non-zero code without guessing at it", () => {
    expect(availability(1)).toEqual({ label: "Unavailable", tone: "negative" });
    expect(availability(16)).toEqual({ label: "Unavailable", tone: "negative" });
  });

  it("says nothing when the store has no code for him", () => {
    expect(availability(null)).toEqual({ label: "Unknown", tone: "neutral" });
  });
});
