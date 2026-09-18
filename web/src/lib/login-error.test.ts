import { describe, expect, it } from "vitest";
import { loginErrorMessage, passwordFailureMessage } from "./login-error";

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

describe("passwordFailureMessage", () => {
  it("says the credentials are wrong only when Supabase says so", () => {
    expect(passwordFailureMessage({ status: 400, code: "invalid_credentials" })).toBe(
      "Wrong email or password.",
    );
  });

  it("says to wait when Supabase rate-limits the attempt", () => {
    for (const error of [{ status: 429 }, { status: 429, code: "over_request_rate_limit" }]) {
      expect(passwordFailureMessage(error)).toBe("Too many attempts. Wait a few minutes and try again.");
    }
  });

  // Every other failure (an outage, an unconfirmed address, a 400 without a
  // code) gets a sentence that is true in every one of those cases, and
  // never claims the password was wrong.
  it("falls back to a sentence true for any other failure", () => {
    for (const error of [
      { status: 500 },
      { status: 400 },
      { status: 400, code: "email_not_confirmed" },
      { status: 0, code: "unexpected_failure" },
      {},
    ]) {
      expect(passwordFailureMessage(error)).toBe("Sign-in didn't work. Try again, or use a sign-in link.");
    }
  });
});
