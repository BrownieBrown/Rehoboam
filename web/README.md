# Rehoboam dashboard

A private, read-only view of the bot's Supabase store — Players, Squad &
lineup, Market, and Calibration & health — built with Next.js (App Router)
and deployed on Vercel. Every page is a server component; nothing in `web/`
ever writes to the store, places a trade, or calls the Kickbase API.

The site is **read-only and names no table**. Every query in
`src/lib/queries.ts` reads one of six views —
`rehoboam.web_players`, `rehoboam.web_squad`, `rehoboam.web_session_summary`,
`rehoboam.web_market`, `rehoboam.web_ownership`, `rehoboam.web_calibration` —
defined in migration `rehoboam/store/migrations/007_web_views.sql` in the bot
repo. If a page needs a number the views don't expose, the fix starts there:
add or extend a view in a new numbered migration, apply it, then read it here.
Never widen the site's access with a raw table query.

**Migration 007 must be applied to production, as the admin, before the
site's first deploy** — the same way as every other store migration (see the
bot repo's `CLAUDE.md`, "Store workflow"). Nothing in `web/` runs it for you.

Auth is Supabase Auth, magic-link only (no password, no self-serve sign-up).
The middleware (`src/middleware.ts`) redirects an anonymous request to
`/login`; every page also calls `requireSession()` itself, so a route the
middleware's matcher somehow misses still cannot reach the store. `next build`
runs `scripts/check-secrets.mjs`, which fails the build if `DATABASE_URL` or
anything naming the pooler or the bot role turns up in the client bundle.

## Environment variables

Three, all required, none of them optional:

| Variable                        | What it is                                                                                                                                                                                                | Where it comes from                                                 |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `DATABASE_URL`                  | The `rehoboam_bot` role's connection string, through the Supabase **transaction pooler** (port 6543) — the same role and pooler the bot itself connects through. Server-side only — never `NEXT_PUBLIC_`. | Supabase project → Database → Connection pooling, transaction mode. |
| `NEXT_PUBLIC_SUPABASE_URL`      | The Supabase project's API URL. Public by design.                                                                                                                                                         | Supabase project → Project Settings → API.                          |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | The project's anon key, used only to drive sign-in (magic link) in the browser — it carries no access to the store; that's `DATABASE_URL`'s job, server-side only.                                        | Supabase project → Project Settings → API.                          |

See `.env.example`. For local development, copy it to `.env.local` (already
`.gitignore`d) and fill in real values.

## Local development

```bash
cd web
npm install
npm run dev        # http://localhost:3000, reads DATABASE_URL directly
```

There's no local database to seed and no mocked store: `npm run dev` reads
the real Supabase store through the real `DATABASE_URL`. That cuts both ways
— see "A dry run moves real numbers" below before treating what you see
locally as a fixed snapshot.

Other scripts: `npm run typecheck` (`tsc --noEmit`), `npm run lint`
(`next lint`), `npm test` (`vitest run`, unit tests beside the modules in
`src/lib/`). `npm run build` also runs the secret-bundle guard; it needs a
real `DATABASE_URL` to import the query modules, which is why CI's `web` job
stops at `lint`/`test` and never runs it — Vercel's own build is that gate.

## The smoke test

`web/e2e/smoke.spec.ts` (Playwright) is not part of CI — it targets a
**deployed** preview or production URL, not anything CI can stand up, and
proves the four pages render real rows behind real auth. It does not check
that the numbers are right; the Python tests under `tests/store/` own that.

Capture a signed-in session once per target:

```bash
cd web
npx playwright codegen --save-storage=e2e/.auth.json https://<preview-or-prod-domain>
```

Sign in with the magic link sent to your inbox in the window that opens, then
close it — codegen writes the authenticated cookies to `e2e/.auth.json` on
close. That file is a live session and is `.gitignore`d; never commit it.

Run the smoke test against a target:

```bash
BASE_URL=https://<preview-or-prod-domain> npm run e2e
```

## The Squad page shows a prediction, not a submission

The eleven on `/squad` is the store's best guess at what the bot fielded, not
a record of what it actually submitted — the store doesn't keep that. The
eleven (`in_best_11`) comes from `rehoboam.predictions`, written early in the
session from that session's opening squad snapshot; the formation
(`legal_formation`) comes from `rehoboam.session_facts`, computed later from
a squad the bot re-fetches live at the lineup step. A forced sale (the
league's Top-5 rule) or an emergency buy can happen in between, so the two
can genuinely disagree — the derived D-M-F count won't match the submitted
formation, or a player the derivation can't place shows up under "Other".
When that happens the page says so, in words, instead of presenting a
mismatched eleven as fact. Recording the lineup the bot actually submitted is
a known follow-up on the bot side, not something this page can fix by itself.

## A dry run moves real numbers

`rehoboam status` (and any `--dry-run` session) is read-only about *trading*
— it places no bid, no sale — but it is not read-only about the store. It
writes the same market snapshot, squad snapshots, and prediction rows a live
session would, unconditionally (`_write_league_state`, `_write_league_predictions`
in `rehoboam/auto_trader.py`). Running the bot locally against the real
`DATABASE_URL` can therefore move what the Players and Market pages show,
within the 5-minute cache (`revalidate = 300`) — the same store, the same
views, whoever wrote to it last. The one exception is `/squad`: it and the
sidebar's session summary key off the newest session where `app = 'function' and dry_run = 0`, so a local dry run never becomes "the" session those two
read.

## Redirects and the auth allowlist

Any redirect built from a user-supplied destination — the auth callback's
`?next=`, in particular — must go through `safeNext()`
(`src/lib/safe-next.ts`), which re-resolves the input against the request's
own origin and always returns an absolute, same-origin URL, never a bare path
a second parse could reinterpret. Never hand a raw query param to a redirect.

That said, `safeNext()` is a second line of defense, not the only one.
Supabase's own redirect allowlist (Auth → URL Configuration) is a **security
control, not configuration**: the magic link's `emailRedirectTo` is built
from the incoming request's `Origin` header, and it's Supabase honouring only
allowlisted destinations — not anything this app does — that stops a forged
`Origin` from pointing the magic link somewhere else. Keep that list tight:
the production domain and the `https://*-<project>.vercel.app/auth/callback`
preview pattern, nothing wider.
