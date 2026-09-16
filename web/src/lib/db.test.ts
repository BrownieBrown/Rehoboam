import { describe, expect, it, afterEach } from "vitest";
import { dsn } from "./db";

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
