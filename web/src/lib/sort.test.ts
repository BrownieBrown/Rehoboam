import { describe, expect, it } from "vitest";
import { sortDir, sortKey } from "./sort";

const ALLOWED = ["name", "market_value", "predicted_ep"];

describe("sortKey", () => {
  it("passes an allowed column through", () => {
    expect(sortKey("market_value", ALLOWED, "predicted_ep")).toBe("market_value");
  });
  it("falls back for anything else - the value reaches SQL as an identifier", () => {
    expect(sortKey("drop table", ALLOWED, "predicted_ep")).toBe("predicted_ep");
    expect(sortKey(undefined, ALLOWED, "predicted_ep")).toBe("predicted_ep");
    expect(sortKey("market_value; --", ALLOWED, "predicted_ep")).toBe("predicted_ep");
  });

  // sortKey is the only gate between a query string and `sql.unsafe()` - a
  // sort column can never be a bound parameter, so every one of these must
  // land on the fallback, not merely "not equal to the malicious input".
  it("rejects adversarial identifiers - quotes, comments, whitespace, injection shapes", () => {
    const hostile = [
      "",
      "   ",
      "\t",
      "\n",
      "name\"", // double quote
      "name'", // single quote
      "name`", // backtick
      "name;", // bare semicolon
      "name -- comment",
      "name/*comment*/",
      "name\0", // embedded null byte
      " name", // leading whitespace on an otherwise-allowed column
      "name ", // trailing whitespace on an otherwise-allowed column
      "NAME", // case must match exactly - no case-insensitive allow
      "Market_Value",
      "market_value ",
      "market_value\n",
      "market_value,predicted_ep", // comma-smuggled second identifier
      "market_value)", // paren escape attempt
      "market_value; drop table rehoboam.web_players;--",
      "(select 1)",
      "1=1",
      "__proto__",
      "constructor",
      "toString",
      "market_valueX", // near-miss, not an exact allow-list member
      "market_valu", // truncated near-miss
    ];
    for (const raw of hostile) {
      expect(sortKey(raw, ALLOWED, "predicted_ep")).toBe("predicted_ep");
    }
  });

  it("only matches against the caller's own allow-list, not some other page's", () => {
    // A column valid on one page must not leak through on another page's
    // narrower allow-list, since each page passes its own list.
    expect(sortKey("owner", ALLOWED, "predicted_ep")).toBe("predicted_ep");
  });
});

describe("sortDir", () => {
  it("accepts only asc and desc", () => {
    expect(sortDir("asc")).toBe("asc");
    expect(sortDir("desc")).toBe("desc");
    expect(sortDir("sideways")).toBe("desc");
    expect(sortDir(undefined)).toBe("desc");
  });

  it("rejects near-misses and injection shapes, falling back to desc", () => {
    for (const raw of ["ASC", "Asc", " asc", "asc ", "asc;drop table x", "", "asc\0", "descending"]) {
      expect(sortDir(raw)).toBe("desc");
    }
  });
});
