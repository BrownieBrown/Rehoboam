# ADR-0013 — Behave like day 0: fill every slot at once, spend the spare cash, value money to the season's end

**Status:** Accepted, 2026-11-11 (Marco: "pretend its day 0 … the bot should behave like its day 0
so we can win after the reset"; then "go with your recommendations") · **Amends:** ADR-0010
(capital neutrality yields to spare cash), ADR-0011 (fills now complete in one session), spec
§4.3 (μ's horizon)

## Context

The reset clears every squad and budget; in a cash-only league each manager restarts with no
players and €150–250m (evaluation §7.4). Replayed from that start with €200m over W1's 15 windows
(spec §12, M2-26): without fills the eleven never stands (387 points a matchday, 43 of 75
kickoffs short); filling by points per euro buys a bench (~370); filling and then keeping the
cash, as the capital-neutral core does, scores 653. Capital neutrality was right in W1 from our
own squad (M2-21, the flip book's capital had a yield), and wrong from day 0, where the cash has
nothing to do but become players. μ valued money only until the reset (§4.3), so the same bot
would behave differently before and after it, against ADR-0011.

## Decision

1. **Every slot the squad cannot field is filled in one session** (`core.bids`), open core bids
   counted as filled; in a session with nothing to fill, one upgrade.
1. **The core spends the wallet's spare cash on upgrades** on top of what it displaces
   (`core_spends_surplus`), never the debt allowance; it fills holes in its own eleven
   (`core_fills_holes="core"`), so a flip standing in a starter's slot is replaced by a starter.
1. **Money counts to the season's end**, before and after the reset (the horizon of μ). One set
   of rules, as ADR-0011 asks.
1. The configuration is `backtest.NAMED_POLICIES["eleven + surplus"]`, frozen as `v3-frozen-6`.

## Consequences

- From day 0 (€200m, no squad): +€4.5m, 908 points a matchday, no empty slot. On W1 from our own
  squad, with the season horizon: +€31.3m, 941 points a matchday, no empty slot (two books:
  +€49.8m, 807, 12 short kickoffs).
- It is a trade-off, not a free win: configurations that keep the cash end with more profit
  (ADR-0012's keep rule reports REJECT on money). Marco chose points.
- W1's market is mostly leftovers; after a real reset every player returns, so day-0 points are a
  floor. The league's reset setting (cash only, or a random ~€100m squad plus €30–80m) is
  unconfirmed; €200m cash is the assumption.
