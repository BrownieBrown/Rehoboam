# ADR-0003 — One planner: a level-triggered reconcile loop

**Status:** Accepted, 2026-11-11

## Context

The decision layer is about 60 rules in ~13,500 lines (evaluation §5.1). They run in a fixed order
in which each phase consumes what the last one left (§5.2). Seven concepts have two to eight
copies that disagree (§5.3), and seven rule pairs defeat each other (§5.4). The worst pair is the
debt gate, which allows a debt up to the value of all non-core holdings, against the recovery
planner, which can only repay it with three sales. Every incident added a guard. No guard was
ever removed.

## Decision

One planner replaces the trade and sell phases. Each session it:

1. observes the squad, wallet, open bids, Kickbase listings and kickoff;
1. values everyone (ADR-0002);
1. searches for the kickoff squad T that maximises
   `EP(best legal eleven of T) + μ(N) · (drift earned − premiums paid)`, subject to hard
   constraints C1–C6 (spec §5.3): a legal eleven, at most 15 players, kickoff solvency through
   named sales, Kickbase's debt cap, bids between the expiry floor and the band ceiling, and the
   trade caps;
1. acts on the difference between T and the squad;
1. records the plan, its inputs and the actions.

It is **level-triggered**: the desired state is recomputed from the observed state every
session, the way a Kubernetes controller reconciles, never derived from remembered events. It
plans to the next kickoff and executes only what is due now (receding horizon), so each next
session corrects for auctions lost and values moved.

**One predicate per concept.** Solvency is `desk/solvency.py` and nowhere else; the bid
bounds are `desk/bids.py`. An import-graph test fails the build if `desk/` or `sim/` imports any
legacy decision module (spec §6, I-A1).

## Consequences

- **Debt is planned, not repaid after the fact.** A plan that runs the wallet negative must name
  the sales that bring it back by kickoff. Those sales are disjoint from the kickoff eleven, so
  the locked window can never be forced into a core sale it didn't plan.
- **Open bids count as won for solvency and as lost for fielding.** Each check assumes the
  outcome that hurts it.
- **Exits are no longer rules.** The turn exit, stop-loss, profit targets, rank sell and dead
  weight are all one question: is the player worth more to the plan than his market value in
  cash?
- **The search is a heuristic.** Greedy add/drop/swap in pure Python, so the Function app gets
  no new dependency. The backtester measures its gap to an exhaustive search; a MILP solver
  (scipy/HiGHS) comes only if the gap exceeds 2%.
- **The planner is pure and fully testable.** It does no I/O, so the same function runs in
  backtest, paper and live (ADR-0005).

## Alternatives considered

- **Fix the 60 rules** (unify the copies, add the missing I3 check, re-order the phases). Every
  fix would be correct, and the system would still have no objective. Rules can't express
  "this trade is worth its premium".
- **A MILP from day one.** Exact and elegant, but it adds numpy and scipy to the deploy and makes
  the plan harder to explain on the session board. It is deferred until measurement says the
  heuristic leaves value behind.
