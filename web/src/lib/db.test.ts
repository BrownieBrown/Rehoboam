import { describe, expect, it, afterEach } from "vitest";
import { dsn, sql } from "./db";

const original = process.env.DATABASE_URL;
afterEach(() => {
  process.env.DATABASE_URL = original;
});

describe("dsn", () => {
  it("returns DATABASE_URL when it is set", () => {
    process.env.DATABASE_URL = "postgresql://user:pw@host:6543/postgres";
    expect(dsn()).toBe("postgresql://user:pw@host:6543/postgres");
  });

  it("throws a named error when DATABASE_URL is missing", () => {
    delete process.env.DATABASE_URL;
    expect(() => dsn()).toThrow(/DATABASE_URL is not set/);
  });

  it("refuses a URL that was exposed to the client", () => {
    delete process.env.DATABASE_URL;
    process.env.NEXT_PUBLIC_DATABASE_URL = "postgresql://leaked";
    expect(() => dsn()).toThrow(/DATABASE_URL is not set/);
    delete process.env.NEXT_PUBLIC_DATABASE_URL;
  });
});

describe("sql", () => {
  // postgres.js hands bigint (oid 20) and numeric (oid 1700) back as strings
  // unless a parser is registered, and `num("123")` throws on `.toFixed`.
  // This is the assertion that fails if the `types` option is ever dropped.
  it("parses bigint and numeric columns to numbers", () => {
    for (const oid of [20, 1700]) {
      const parse = sql.options.parsers[oid];
      expect(parse, `no parser for oid ${oid}`).toBeTypeOf("function");
      expect(parse("123")).toBe(123);
    }
    expect(sql.options.parsers[1700]("-12.35")).toBe(-12.35);
    expect(sql.options.parsers[20]("65089670")).toBe(65089670);
  });

  // Pipelined queries through the Supabase transaction pooler hang: two
  // pages' worth of parallel queries (3 connections, 6 queries) stalled on
  // the second round every time against the real store, and completed 90 of
  // 90 pairs with pipelining off. This fails if the option is ever dropped.
  it("never pipelines a second query onto a busy connection", () => {
    // Missing from postgres.js's option types, present at runtime.
    const options = sql.options as unknown as { max_pipeline: number };
    expect(options.max_pipeline).toBe(0);
    expect(sql.options.prepare).toBe(false);
  });
});
