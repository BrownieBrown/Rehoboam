# ADR-0001 — Trade for drift with a full squad

**Status:** Accepted, 2026-11-11 · **Supersedes:** v2 design §3 ("own the level, hold")

## Context

Two strategies were on the table (evaluation §9.1):

- (a) **The leaders' game.** Trade every week for market-value drift, keep 13–14 players, and
  field the best eleven of what you hold.
- (b) **v2's hold-the-level.** Few trades, long holds, no profit-target sales.

The evidence, from evaluation §6–§7:

- Points correlate with team value at ρ 0.90, and team value with transfer P&L at ρ 0.80.
  Money is how points are bought here.
- Every manager overpays. The leaders' whole profit is drift while held: +85m to +97m in
  2026/27, +248m for last season's winner. Ours is −5m.
- They buy after rising nights (71–80%), pay a median 6–7%, sell when the run stops, hold 13–14
  players, and win 62–71% of quick resales. We win 24%.
- Our −66.45m this season is −72.12m of premium plus +7.51m of drift. The players we bought rose
  while we held them, and we lost the money at the bid.
- Marco's own rule after Marino ("there are always players on the market to fill a spot") and
  his hand trading during autonomy (+4.2m against the bot's −23.1m) are strategy (a).

v2 derived (b) from our own losses only and never benchmarked against the managers who win.

## Decision

The bot trades for drift. It holds a squad of up to 15, targeting 13–14, and fields the best
eleven of it. Any player is bought when his expected drift and points beat his premium. Any
player is sold when holding him is worth less than his market value in cash.

## Consequences

- **Hold-the-level is still available.** When no listing's drift clears its premium (a deflating
  market, the second half), the planner trades nothing. Strategy (b) is what (a) does when the
  edge is gone, so it needs no mode switch.
- **Everything depends on predicting drift.** If drift can't be predicted out of sample, the
  strategy can't be executed by a bot. Spec §10's M1 kill criterion tests exactly that, before
  the planner is built.
- **More trades means more premiums paid.** The premium ceiling (ADR-0004) and the objective's
  premium term (spec §5.2) keep each trade's toll explicit.
- **The squad grows from 10 to 13–14.** The bench is inventory, and it covers the eleven.

## Alternatives considered

- **(b) hold-the-level.** No winner here plays it; it was rejected on the league's evidence.
- **A hybrid with fixed books** (core held, trading book flipped). This is what ran from
  2026-11-01 (`services/books`). The book a player belonged to was decided by rank at a moment
  and then frozen; ADR-0002 removes that.
