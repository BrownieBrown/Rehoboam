# Rehoboam dashboard

A private, read-only view of the bot's Supabase store — Players, Squad &
lineup, Market, and Calibration & health — built with Next.js (App Router)
and deployed on Vercel. Every page is a server component; nothing in `web/`
ever writes to the store, places a trade, or calls the Kickbase API.

Players and Market also show `Next MV`, the forecast for tonight's ~22:00
market-value update — the percent change over the signed euro amount, a dash
when there is no live forecast (overnight included, until the morning run
writes tomorrow's). Calibration & health shows how those forecasts have
scored against "no change".

The site is **read-only and names no table**. Every query in
`src/lib/queries.ts` reads one of seven views —
`rehoboam.web_players`, `rehoboam.web_squad`, `rehoboam.web_session_summary`,
`rehoboam.web_market`, `rehoboam.web_ownership`, `rehoboam.web_calibration`,
`rehoboam.web_mv_accuracy` — defined in migrations
`rehoboam/store/migrations/007_web_views.sql` and `008_mv_forecast.sql` in
the bot repo; migration 008 also defines `web_mv_forecast`, left-joined into
`web_players` and `web_market` for their `next_mv_change`/`next_mv_pct`
columns rather than queried on its own. If a page needs a number the views
don't expose, the fix starts there: add or extend a view in a new numbered
migration, apply it, then read it here. Never widen the site's access with a
raw table query.

**Migration 007 must be applied to production, as the admin, before the
site's first deploy**, and **migration 008 before any deploy that reads its
columns** (`Next MV`, the accuracy section) — the same way as every other
store migration (see the bot repo's `CLAUDE.md`, "Store workflow"). Nothing
in `web/` runs it for you.

Auth is Supabase Auth: email and password first
(`signInWithPassword` in `src/app/login/actions.ts`), with a one-time link
to the inbox behind "Forgot your password?" as the fallback. Signing in is
only half of access control, though: the site is **single-owner in code**.
The middleware (`src/middleware.ts`), the password sign-in action, the auth
callback (`src/app/auth/callback/route.ts`) and `requireSession()`
(`src/lib/auth.ts`) all check the signed-in user's email against
`ALLOWED_EMAILS` (`src/lib/allowed-emails.ts`) and refuse anyone who isn't
on it — the middleware, the sign-in action and the callback sign the account
out and redirect to
`/login?error=not-allowed`; `requireSession()` can only redirect there (a
server component can't clear cookies), which is fine because the middleware
signs the account out on the very next request regardless. Either way, a
request the middleware's matcher somehow lets through still gets no data
from `requireSession()`. `/login` and `/auth/callback` sit outside the
`(app)` route group and never render the sidebar. `next build` runs
`scripts/check-secrets.mjs`, which fails the build if `DATABASE_URL`,
`ALLOWED_EMAILS`, or anything naming the pooler or the bot role turns up in
the client bundle.

The database client (`src/lib/db.ts`) sets three options that are not
optional. `prepare: false`, because the transaction pooler rejects prepared
statements. `types`, because postgres.js returns bigint and numeric columns
as strings. `max_pipeline: 0`, because a second query sent down a busy
connection stalls behind the pooler; the pages run their queries in
parallel, and the Market page stalled on it during the first local run.

## Environment variables

Four, all required, none of them optional:

| Variable                        | What it is                                                                                                                                                                                                                                                 | Where it comes from                                                 |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `DATABASE_URL`                  | The `rehoboam_bot` role's connection string, through the Supabase **transaction pooler** (port 6543) — the same role and pooler the bot itself connects through. Server-side only — never `NEXT_PUBLIC_`.                                                  | Supabase project → Database → Connection pooling, transaction mode. |
| `NEXT_PUBLIC_SUPABASE_URL`      | The Supabase project's API URL. Public by design.                                                                                                                                                                                                          | Supabase project → Project Settings → API.                          |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | The project's anon key. It drives sign-in (password or link), and the middleware and `requireSession()` use it on every request to check, refresh and clear the session. It carries no access to the store; that's `DATABASE_URL`'s job, server-side only. | Supabase project → Project Settings → API.                          |
| `ALLOWED_EMAILS`                | Comma-separated email addresses allowed to use the site, matched exact (trimmed, lower-cased) after sign-in. Server-side only — never `NEXT_PUBLIC_`. **Empty or unset locks everyone out** — fails closed, not open.                                      | You choose it — the owner's own email address(es).                  |

See `.env.example`. For local development, copy it to `.env.local` (already
`.gitignore`d) and fill in real values.

### Owner steps: keeping this a one-user site

`ALLOWED_EMAILS` is the code-level gate, but it is only half the lock — the
other half lives in the Supabase project itself, and both must be done:

1. Set `ALLOWED_EMAILS` to the owner's email address (in Vercel's project
   env vars for the deployed site, and in `.env.local` for local dev).
1. In the Supabase dashboard, **Authentication → Sign In / Providers**,
   under **User Signups**, turn **Allow new users to sign up** off — this is
   a project-wide auth setting, not something inside the Email provider's
   own panel (leave the Email provider's **Confirm email** switch as it is;
   it's unrelated). The publishable anon key ships to every browser, so
   anyone holding it can call Supabase's sign-up endpoint directly
   regardless of what this app's own login form does (`shouldCreateUser: false` only stops the link form itself from creating accounts).
1. Create the owner in **Authentication → Users → Add user → Create new
   user**, with the owner's address, a long password, and **Auto Confirm
   User** ticked. The dashboard creates the account through the admin API,
   so it works with sign-ups already off — no window in which someone else
   could register the address first. One way to change the password later
   is to delete the user and create it again; nothing in the store
   references the auth user's id.
1. Use a long password. The publishable key lets anyone try passwords
   against Supabase's token endpoint directly, with Supabase's per-IP rate
   limit as the brake. That endpoint also serves session refreshes, and
   attempts made through this site's form come from the server's addresses.
   Hammering the form can therefore get the owner's refresh refused, and a
   refused refresh after the access token has expired signs the owner out
   until the limit resets.

`ALLOWED_EMAILS` keeps the site closed even if sign-ups are ever switched
back on — the two together are the access model, and neither is a
substitute for the other.

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

Sign in with the owner's email and password in the window that opens, then
close it — codegen writes the authenticated cookies to `e2e/.auth.json` on
close. That file is a live session and is `.gitignore`d; never commit it.

Run the smoke test against a target:

```bash
BASE_URL=https://<preview-or-prod-domain> npm run e2e
```

## The Squad page shows a prediction, not a submission

The eleven on `/squad` is the store's best guess at the eleven for the
*coming* matchday, not a record of what the bot actually fielded. The store
does eventually record that: Kickbase's own `/teamcenter` truth, written to
`rehoboam.matchday_lineup_results` by `Trader` once every match of that
matchday has finished. But that write is retrospective — it only exists
after the matchday concludes — and none of the six `web_*` views this page
reads join that table. What the page shows instead: the eleven
(`in_best_11`) comes from `rehoboam.predictions`, written early in the
session from that session's opening squad snapshot; the formation
(`legal_formation`) comes from `rehoboam.session_facts`, computed later from
a squad the bot re-fetches live at the lineup step. A forced sale (the
league's Top-5 rule) or an emergency buy can happen in between, so the two
can genuinely disagree — the derived D-M-F count won't match the submitted
formation, or a player the derivation can't place shows up under "Other".
When that happens the page says so, in words, instead of presenting a
mismatched eleven as fact. Wiring the squad page to that retrospective
record — so a finished matchday can show what was actually fielded, not just
what was predicted for it — is a known follow-up, and it needs no change to
how the bot behaves: the bot already writes that table, so the work is a new
numbered migration in this repository that exposes it through a `web_*` view,
and the page that reads the view.

## A dry run moves real numbers

`rehoboam status` (and any `--dry-run` session) is read-only about *trading*
— it places no bid, no sale — but it is not read-only about the store. It
writes the same market snapshot, squad snapshots, and prediction rows a live
session would, unconditionally (`_write_league_state`, `_write_league_predictions`
in `rehoboam/auto_trader.py`). Running the bot locally against the real
`DATABASE_URL` can therefore move what the Players and Market pages show on
the next page load — the same store, the same views, whoever wrote to it
last. There is no page cache to wait out: every page reads the session
cookie, which makes it render per request, so every load queries the views
afresh. The one exception is `/squad`: it and the
sidebar's session summary key off the newest session where
`app = 'function' and dry_run = 0`. The CLI always writes `app = 'cli'`
(`rehoboam/cli.py`) — for `auto` as much as for `status` — so *any* local
run fails that filter regardless of `--dry-run`; only a live Azure Function
session ever becomes "the" session those two read.

## Redirects and the auth allowlist

Any redirect built from a user-supplied destination — the auth callback's
`?next=`, in particular — must go through `safeNext()`
(`src/lib/safe-next.ts`), which re-resolves the input against the request's
own origin and always returns an absolute, same-origin URL, never a bare path
a second parse could reinterpret. Never hand a raw query param to a redirect.

That said, `safeNext()` is a second line of defense, not the only one.
Supabase's own redirect allowlist (Auth → URL Configuration) is a **security
control, not configuration**: the fallback link's `emailRedirectTo` is built
from the incoming request's `Origin` header, and it's Supabase honouring only
allowlisted destinations — not anything this app does — that stops a forged
`Origin` from pointing the link somewhere else. Keep that list tight:
the production domain and the `https://*-<project>.vercel.app/auth/callback`
preview pattern, nothing wider.
