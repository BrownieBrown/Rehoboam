# ADR-0004 — The premium is a cost, capped at the band's median

**Status:** Accepted, 2026-11-11 · **Supersedes:** the core-buy bid (2026-11-03), the must-have
exemption from the break-even cap, the urgency bump

## Context

Across our 60 buys this season (evaluation §6.1):

- buys at 0–5% over market value made +7.37m;
- buys at 10–20% lost −29.07m;
- buys at 20% and above lost −33.65m.

The leaders pay a median 6–7%. At 15m+ we paid +16.1% when we won, against +8.6% by the
winners when we lost (§4.3). On 314 league buys at 15m+, paying more bought no extra points while
resale fell almost one-for-one with the premium (CLAUDE.md, 2026-10-25). Every mechanism that
raised our bids ("bid to win" for core buys, the must-have exemption, the urgency bump, the
learned override) was justified by the auctions it lost. None was weighed against what winning
cost.

## Decision

- **Ceiling.** The league's median winning premium for the price band, from `services/win_curve`
  (ours excluded, last 120 days). No class of player is exempt.
- **Floor.** `mv_at_expiry`, the forecast market value when the auction settles. Kickbase
  declines a bid below it (break review, 2026-10-07).
- **No bid** when the floor exceeds the ceiling.
- **Between the two,** the premium p maximises `F(p) · (V − p · MV)`, where F is the band's win
  curve and V is the player's marginal value to the plan, in euros.

## Consequences

- We lose more auctions. At the median we win about half the contested ones; a lost auction
  costs nothing, and the next session plans again with the next listing.
- A must-have with a falling forecast can be unbuyable. That is the intended outcome: the
  league's data says the overpay bought no points.
- `PREMIUM_CEILING_QUANTILE` is a single knob; the backtest reports p50 against p60 and p75 as
  ablations, and the knob changes only with a run attached (spec §9).

## Alternatives considered

- **No ceiling, only the objective's premium term.** In theory the objective already prices the
  premium. In practice V carries the scorer's selection bias (+26.7 on our picks), so an
  uncapped `argmax` would bid up exactly the overestimates. The ceiling is the guard against our
  own model's optimism.
- **The ceiling at p75** (`derive-ceilings`' proposal). That sizes a cap to win auctions, not to
  profit from them.
