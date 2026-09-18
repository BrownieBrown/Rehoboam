import { describe, expect, it } from "vitest";
import { matchStatus } from "./match-status";

describe("matchStatus", () => {
  it("names every known status", () => {
    expect(matchStatus(5)).toBe("started");
    expect(matchStatus(3)).toBe("came on");
    expect(matchStatus(4)).toBe("unused sub");
    expect(matchStatus(1)).toBe("not in squad");
    expect(matchStatus(0)).toBe("not played");
    expect(matchStatus(null)).toBe("not played");
  });

  it("names an unknown status by its number instead of guessing", () => {
    expect(matchStatus(2)).toBe("status 2");
    expect(matchStatus(99)).toBe("status 99");
  });
});
