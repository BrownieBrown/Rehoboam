# Autonomous mechanics, approved judgment: Telegram-gated trades and a daily email

Date: 2026-08-23
Status: design, approved in conversation, not yet planned
Related: REH-52, REH-71, REH-90, REH-91, REH-92, REH-93

Supersedes an earlier draft of this file that put an LLM in the decision path on
every run. That was dropped on cost (~$25/month) once it became clear the
reasoning Marco wants is already derivable from data the bot holds. **This design
adds no paid dependency.**

## Why

Across 2026-08-21 to 2026-08-23 the bot produced five wrong decisions. Every one
was caught by Marco looking at a single player and asking why — never by a test,
a review, or the season replay.

| what happened                                                       | what the bot could not see                                         |
| ------------------------------------------------------------------- | ------------------------------------------------------------------ |
| A long-term-injured player named in the starting eleven             | its own `player_details.st`, which it never read                   |
| Two players with **zero** Bundesliga appearances ranked 1st and 2nd | that their fitted form was 2. Bundesliga                           |
| A Bayern midfielder skipped below the buy floor                     | that his availability rested on a three-month-old bench appearance |
| A bid on a Hamburg striker while his value fell 34%/14d             | that Hamburg are newly promoted                                    |
| Sabitzer rated the squad's second-best player                       | that he is likely leaving the league                               |

Four of the five are now fixed. **The fifth cannot be**: the information was never
in the data. It was in Marco's head and in the football press.

That is the division this design draws. Where the bot has the facts, it acts
alone. Where judgment needs information the bot structurally cannot hold, a
human decides — and the bot's job is to make that decision cheap by presenting
the complete case.

## Goals

1. The bot runs lineup and profit trading unattended.
1. Squad-improving trades are proposed with the full case and executed only on
   Marco's approval.
1. Approval takes one tap and executes immediately.
1. A daily email makes the state visible without asking.
1. No new paid dependency.

## Non-goals

- **An LLM in the decision path.** Dropped on cost. Every element of the
  reasoning Marco asked for — why a buy, why it improves the lineup, why this
  price — is derivable from data already fetched. An LLM would add only what the
  bot cannot know (injury news, transfer rumours); that is a later bolt-on,
  cheap if called only when a trade is proposed rather than every run.
- **Microservices or Kubernetes.** The workload is two minutes a day, AKS would
  cost €30–70/month against a free deployment, one person maintains this, and not
  one of the five failures was a coupling problem.
- **A second Function app.** The webhook is an HTTP trigger on the existing
  `func-rehoboam`, alongside the timer.

## Design

### 1. What the bot does alone

**Lineup, every run.** Existing pipeline, including the injury override shipped
2026-08-22 — so no repeat of a long-term-injured player being fielded.

**Profit trading, in two ringfenced slots.** Buy at `market_value × 1.01`, the
league's minimum legal bid, on players with a strong recent uptrend; hold days;
sell at market value.

The measured basis, from 161,492 daily market values across 531 players: players
rising >10%/7d in the €5–15M band returned **+3.7% net of toll over three days at
an 82% win rate**, while flat-or-falling players lost ~2.4%. The historical
−€55.3M across 151 flips is explained by entry premium — at the +12.2% the bot
used to pay, that same +3.7% becomes −9.8%.

**Scope note:** the flip path exists (`profit_trader.py`) but its buy side is
disabled (`ENABLE_FLIP_BUYS=false`, set 2026-08-22 pending REH-71) and it does
not implement the MV+1% discipline the edge depends on. Re-enabling and
constraining it is separate work; until it lands the two slots go unused.

### 2. What needs approval

Any trade whose purpose is improving the starting eleven. These are rare — on
2026-08-23 exactly **1 of 22** candidates was viable — so approvals should be a
few a week, not daily.

### 3. The proposal

Sent to Telegram with an inline keyboard (Approve / Reject). Content, all
derived from data the bot already has:

```
BUY Aleksandar Pavlović — €32,608,485

WHY THIS PLAYER
  EP 82.6, highest available. Bayern, healthy (status 0).
  Fitted quality 1.43 — 43% above the positional average.

WHY IT IMPROVES THE LINEUP
  Enters the best eleven, displacing Klaas (25.5).
  Best-eleven total 486.2 → 543.4 = +57.2 points per matchday.

WHY THIS PRICE
  Market value €32,285,629; bid +1.0%. Trend +1.9%/7d.
  Budget €95,317,114 → €62,708,629. Positive at kickoff.

RISKS
  Availability is the 59% generic prior — no recent match evidence.
  Two slots reserved for flips; this uses a squad slot, leaving 1.
```

Every number is one the bot computed and can show its working for. The "risks"
section is generated from known-weak inputs — a cold-start quality, a stale
availability prior, a falling market value — so the message never presents a
guess as a fact.

### 4. Approval by webhook

An HTTP-triggered function on `func-rehoboam`, registered as the Telegram
webhook. On tap it:

1. **Authenticates.** Telegram's `X-Telegram-Bot-Api-Secret-Token` header plus
   the Azure function key. A public URL that executes trades needs both.
1. **Re-validates.** Between proposal and tap the world moves: market values
   update daily after 10:00, the auction may have resolved, budget may have
   changed, slots may have filled. Re-fetch and re-check price, budget, slots
   and squad legality. **Never execute against the proposal's stale numbers.**
1. **Runs the safety gate** (below).
1. **Executes**, and replies in the same Telegram thread with the outcome —
   including "no longer available" or "price moved beyond the cap", which are
   normal outcomes rather than errors.

**Idempotency.** Each proposal carries an id; a second tap on the same proposal
is a no-op with an explanatory reply. Telegram retries callbacks, and a double
execution here is a double purchase.

**Expiry.** A proposal not acted on within a configurable window is marked stale
and rejected on tap, rather than executing against day-old reasoning.

### 5. The safety gate

A pure function, applied to anything that executes — autonomous or approved:

- Budget ≥ 0 at kickoff after the trade; a negative balance scores zero for the
  whole matchday
- Bid ≤ market value × (1 + `Settings.max_overbid_pct`)
- Never leave fewer than 11 fieldable players
- No player with injury status 4 or 256 in the lineup
- Flip buys confined to their two slots
- Spend and trade counts within per-run limits

All `Settings` fields, `.env`-tunable without a deploy. Every rejection is logged
with its reason and appears in the daily email — a limit that keeps firing is a
signal, and silence about it is exactly what let eleven dropped capabilities
survive.

### 6. The daily email

One message per day: current lineup with EP per player and anything flagged
injured or uncertain; squad and budget; the buyable market with EP, market value
and trend; open bids and pending proposals; what executed in the last 24 hours;
and any safety-gate rejections.

Sent over SMTP with credentials as Key Vault references surfaced as app settings
— the pattern `KICKBASE_PASSWORD` already uses. Nothing lands in the repository.

### 7. Failure behaviour

Telegram or SMTP being unreachable must never block trading or lineup setting.
Notification failures are logged and swallowed, consistent with the project's
existing best-effort learning calls. The reverse also holds: a failed trade
still sends its email.

## Verification

The safety gate and the proposal renderer are pure functions and get full unit
coverage. The webhook handler is tested against forged and replayed callbacks —
authentication and idempotency are the security surface, and a public endpoint
that spends money deserves adversarial tests rather than happy-path ones.

`replay-season` measures none of this, and is not expected to: nothing here
changes scoring. It remains the gate for scoring changes only.

## Risks

- **A public endpoint that spends money.** Mitigated by two independent secrets,
  idempotency, expiry, and re-validation. This is the highest-risk element of
  the design and deserves the most testing.
- **Approval fatigue.** If proposals arrive more often than expected, Marco is
  back in the daily loop the design exists to reduce. The daily email should
  report proposal volume so this is visible early.
- **Stale reasoning.** Handled by re-validation and expiry, but a proposal
  approved at the edge of its window may still execute on a market that has
  drifted. Expiry should start short.
- **The bot's judgment is still the ceiling for what gets proposed.** If the
  scorer never surfaces a good player, no approval flow will. REH-90 through
  REH-93 remain worth doing underneath this.
