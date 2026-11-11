# ADR-0002 — Value every player every session; no stored labels

**Status:** Accepted, 2026-11-11

## Context

v2's trading code stored a player's role at the bid: `tracked_purchases.intent` (`flip` or
`points`), core book membership, rank hold. Each label decided which rules applied to him
afterwards, and none was re-checked against the present:

- **Marino** (evaluation §4.4) was a `points` buy. The turn exit reads only `intent='flip'`, and
  the profit loop skips the best eleven, so he rode four falling nights. The turn exit would
  have sold him near 8.44m; Marco sold him by hand at 6.68m.
- **The recovery planner** ordered sales by cost basis (profit before loss), which sold Regeer and
  Quansah, our two best starters, because they were in profit (§4.2).
- **The scorer is biased on exactly the players its EP-per-euro rules select** (+26.7 on ours
  against +0.8 on the rest, §4.1), and no rule ever revisited a buy on that evidence.

## Decision

Every session gives every held player and every Kickbase listing a `Valuation`, computed from
scratch:

- `ep_next`: the scorer's expected points at the next kickoff;
- `drift_mean` and `drift_low`: expected 7-day market-value change and its p10, from a drift table
  fitted on `mv_series` (spec §4.2);
- `mv_at_expiry`: the nightly forecast's value when an auction settles.

Nothing about a player's role is stored. The cost basis is kept for reporting only; no decision
reads it (it is a sunk cost).

## Consequences

- "Flip", "points buy", "core" and "trading book" stop existing as states. A rising
  midfielder bought for points is sold at the turn if the plan values cash more; a "flip" that
  turns out to be the best striker on the market stays.
- The drift table becomes the second model the bot depends on, next to the scorer. It gets the
  scorer's treatment: fitted on stored data, validated out of sample, refit on a schedule, and
  reported on the Calibration page.
- The price blend (folding market value into EP) is not adopted by assumption. It is a backtest
  ablation, because a regression fit didn't remove the selection bias (§4.1).

## Alternatives considered

- **Keep the labels and re-check them each session.** That is two sources of truth, the label
  and the re-check, with rules choosing between them. It is the "one rule, many copies" pattern
  the evaluation counted six times (§5.3).
