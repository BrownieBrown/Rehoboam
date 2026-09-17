import { defineConfig } from "@playwright/test";

/**
 * Targets a deployed preview or production URL, never a local server: there
 * is no `webServer` block here, deliberately. The smoke test isn't proving
 * `next build` works — CI's `web` job doesn't build at all, and Vercel's own
 * build is that gate (see the README) — it's proving the deployed site
 * renders real rows against the real store behind real auth.
 *
 * `storageState` points at a file this repo never commits (`.gitignore`d):
 * capture it once per target by signing in with `npx playwright codegen`,
 * see README.md.
 */
export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: process.env.BASE_URL,
    storageState: "e2e/.auth.json",
  },
});
