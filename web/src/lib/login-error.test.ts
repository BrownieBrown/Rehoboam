import { describe, expect, it } from "vitest";
import { loginErrorMessage } from "./login-error";

describe("loginErrorMessage", () => {
  it("returns the fixed sentence for each known value", () => {
    expect(loginErrorMessage("link")).toBe("That sign-in link didn't work. Request a new one.");
    expect(loginErrorMessage("not-allowed")).toBe("That account can't use this site.");
  });

  it("returns null for null, empty, unknown, wrong-case, and near-miss values", () => {
    for (const value of [null, "", "nope", "LINK", "link ", " link", "Not-Allowed"]) {
      expect(loginErrorMessage(value)).toBeNull();
    }
  });

  // The defect this guards: `MESSAGES[value]` on a plain object literal also
  // finds every key Object.prototype provides. `__proto__` resolves to
  // `Object.prototype` itself (not a string) and crashes React; the method
  // names below resolve to functions and render as an empty paragraph.
  // None of these may ever produce a truthy, non-null return.
  it("returns null for every inherited Object.prototype key", () => {
    for (const value of ["__proto__", "constructor", "toString", "hasOwnProperty", "valueOf"]) {
      expect(loginErrorMessage(value)).toBeNull();
    }
  });
});
