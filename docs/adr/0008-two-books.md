# ADR-0008 — Two books: a core for points, a flip book for drift

**Status:** Accepted, 2026-11-11 (Marco: "go with your recommendation") · **Amends:** ADR-0003

## Context

ADR-0003 put every decision in one planner: a level-triggered loop maximising
`U = EP of the eleven + μ·(Σ value × drift − Σ premiums)` every session. Over W1 it made
+€14.3m, against +€10.8m for holding the starting squad (spec §12, M2-17). It re-solves a 7-day
objective every 12 hours, so the policy it executes is a chain of one- or two-day holds, each
paying a premium priced for seven days (M2-13, M2-14). Four attempts to repair the churn inside
the planner failed: debt cover, a 7-day commitment, value bids, a commitment that yields to
points (M2-13 to M2-15).

The league's leaders trade differently. They hold a riser through his run, sell at the turn, and
fund it on debt they repay before the kickoff (evaluation §7.3). Encoded as three rules
(`desk/flipbook.py`), that trade made +€47.4m in the same simulator, held 6.1 days, and earned
+€38.6m of trading profit against Bebè's +€37.9m (M2-17, M2-18).

The two halves are different kinds of state. The eleven is a desired state: "the best legal
eleven for the next kickoff", correctly re-derived from scratch every session. A flip is a
position with a lifecycle: bought, held, sold at the turn. A loop that re-derives everything
every session has nowhere to keep a lifecycle, and that is the churn.

## Decision

1. **Two books share one wallet and one squad.**
   - **The core** is every player we hold that we did not buy as a flip. It is judged on points:
     the best legal eleven, upgraded when a listing raises the eleven's expected points by
     enough to pay its premium. A core player the eleven no longer uses is sold.
   - **The flip book** is the players we won as flips and still hold. It owns the free squad
     slots and the debt allowance (Kickbase's 33% of squad value). It buys the listings with the
     highest expected drift that clears its premium, sells each at the first session that sees
     a night without a rise, and the last session before a kickoff repays the wallet to zero:
     flips first, then the core's least expected points per euro.
1. **The core has first claim** on the wallet and the debt allowance, for at most one upgrade a
   session that adds at least 10 expected points a matchday to the eleven (`desk/core.py`); the
   flip book uses what is left.
1. **Which book a player belongs to is a fact, not a label** (ADR-0002 holds): a flip is a player
   we won from a flip bid, read from our own transfer history.
1. **Every rule stays pure** in `rehoboam/desk/`, with its adapter in `sim/` and later the live
   session.

## Consequences

- ADR-0003's single objective survives inside the core, where it is a desired-state problem. The
  flip book is a small state machine beside it, with three rules and their measured evidence.
- The flip book alone never improves the eleven. Before the reset it still scored +20 points a
  matchday over our real record; from the reset the core must build the squad from nothing.
- Shared resources need an order: cash goes to the core first, slots to whoever bids first in a
  session (the core). If slots bind the flip book (99% of sessions in M2-18), every core upgrade
  costs a flip slot until the displaced starter is sold.
- The legacy bot also had "two books" (`services/books`, 2026-11-01). The difference is scope: it
  sat on ~60 rules; this one is the whole decision layer.
