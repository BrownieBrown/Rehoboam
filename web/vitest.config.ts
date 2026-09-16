import { defineConfig } from "vitest/config";

export default defineConfig({
  // Mirrors Next's server-bundler resolve condition so importing db.ts under
  // plain Node (no Next build step) resolves `server-only` to its no-op
  // export instead of the throwing one — the same reason `db.ts` needs
  // `import "server-only"` to be a build-time guard, not a test-time crash.
  resolve: { conditions: ["react-server"] },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    // db.ts builds its pooled client at import time (`sql = postgres(dsn(), ...)`),
    // so importing it needs *some* DATABASE_URL before any test body runs.
    // `postgres()` is lazy — this placeholder never opens a socket. Each test
    // still exercises dsn() by setting process.env.DATABASE_URL itself.
    env: { DATABASE_URL: "postgresql://placeholder:placeholder@localhost:5432/postgres" },
  },
});
