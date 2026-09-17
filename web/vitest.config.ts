import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // Mirrors the `@/*` -> `./src/*` mapping tsconfig.json declares for the
    // compiler; Vite doesn't read tsconfig `paths` on its own, and
    // middleware.ts (and anything else under test that imports via `@/…`,
    // e.g. middleware.test.ts) needs it resolved at test time too.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    // Mirrors Next's server-bundler resolve condition so importing db.ts under
    // plain Node (no Next build step) resolves `server-only` to its no-op
    // export instead of the throwing one — the same reason `db.ts` needs
    // `import "server-only"` to be a build-time guard, not a test-time crash.
    conditions: ["react-server"],
  },
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
