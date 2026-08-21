# REH-72 — What a 37,000-point season looked like: results

Ticket: https://linear.app/jovily/issue/REH-72
Date: 2026-08-21
Status: questions 1 and 3 answered. Question 2 (reconstruct their squads) remains
blocked — see §6.

______________________________________________________________________

## 0. The answer, in one line

**The gap is ownership, not fielding — and the mechanism is that the bot cannot
pace its capital across a season.** We extract more points per euro of squad
than the champions do. We simply own a quarter as much, because the bidding
rule answers "what fraction of my budget does this signing justify?" without
anything ever asking "how much of my season should one player consume?"

## 0a. CORRECTION (2026-08-21, same day): the bot is not the only actor

Marco reports that the large purchases in the 2025/26 season — including the
EUR 71m one — were **his own manual trades, not the bot's**. `manager_transfers`
records a manager's transfers without recording *who initiated them*, so for
manager `3616202` the bot's actions and Marco's are indistinguishable in this
data. That was checkable and was not checked before §4 and §5 were written.

**Retracted:** every attribution of the historical capital collapse to the bot.
The EUR 75.8m end-of-season team value, the +EUR 69.8m net liquidation and the
−EUR 55.3m of round-trip losses (REH-75) are what *the account* did. How much of
it the bot did is unknown and, with this schema, unknowable.

**Still standing, because it does not depend on attribution:**

- §2, the shape of the gap. Points scored are points scored.
- §3, points per EUR 100m. An outcome ratio, not an actor claim.
- §4's *trajectory* as a description of the account's capital.
- **§6 entirely.** That evidence comes from the replay, where the bot is the
  only actor and no manual trade exists. The simulated bot chose to commit
  EUR 71m of an EUR 80m ceiling and was then locked out for the season. That is
  bot logic, and it is the finding REH-85 acts on.

The §5 comparison against the winners also weakens: their columns may likewise
mix manual and automated behaviour, since every rival is a human. It is a
comparison of *accounts*, not of strategies, and should be read that way.

Recording provenance on transfers going forward would retire this whole class of
uncertainty — it is the same gap REH-75 §0 hit from the other side, where an
EP-driven squad buy and a profit flip were indistinguishable in `flip_outcomes`.

______________________________________________________________________

## 1. Evidence handling

Read-only over `logs/bid_learning.db` and `logs/training_corpus.db`. No API
calls. Replay figures come from `rehoboam replay-season` at commit `9783d28`
(the first commit at which the replay scores through the live `compose_ep` —
before REH-84 the harness could not see scorer changes at all).

**A correction to the ticket's data inventory.** REH-72 cites
`player_transfers` with "`counterparty_id` on 3,387 of 6,610 type-2 rows
(51%)". **That table does not exist.** What exists is `manager_transfers`, 426
rows. REH-74 further claims that table covers only `2026-04-06 → 2026-05-16`;
it actually spans **2026-01-03 → 2026-05-16, four and a half months**. Both
claims were repeated in this analysis before being checked, and both were
wrong. The 4.5-month window is the more consequential correction, because it
covers precisely the period in which our capital collapsed.

## 2. The gap is steady, not bursty

From `league_rank_history` (938 rows, 14 managers, 34 matchdays):

|                           |        ours | winners' best |
| ------------------------- | ----------: | ------------: |
| Mean matchday points      |         770 |         1,262 |
| Median gap per matchday   |             |           450 |
| Mean gap per matchday     |             |           492 |
| Matchdays we won          | **4 of 34** |               |
| Worst single matchday gap |             |         1,360 |

Median and mean sit close together, so the deficit is not a handful of
disasters — it is a persistent, structural shortfall. This rules out the
tempting story that the season was lost on the matchday-14 bankruptcy. That
event is real and exact (+688 in every replay attribution) but it is one
matchday of a 34-matchday deficit.

## 3. We field better than they do

Points per matchday per EUR 100m of squad value:

|         |    mean | median |   n |
| ------- | ------: | -----: | --: |
| ours    | **517** |    514 |  34 |
| winners |     433 |    418 |  68 |

**Caveat, stated before anyone quotes this.** Points-per-value structurally
flatters cheap squads: you field eleven players regardless of what you own, so
owning more buys *better* players rather than proportionally more points. The
naive counterfactual — 517 × 3 = 1,551 points a matchday — is wrong and must
not be used. What survives the caveat is the **direction**: our selection and
lineup work is not the binding constraint. The replay agrees from the other
side, putting the squad-and-lineup term between +100 and −326 depending on
configuration, i.e. approximately zero.

## 4. Capital: parity, then collapse

Team value by matchday (EUR m):

|      matchday |     ours | winner 1 | winner 2 |
| ------------: | -------: | -------: | -------: |
|             1 |    128.5 |    119.2 |    164.4 |
|            11 |    207.8 |    268.0 |    234.5 |
| 19 (our peak) |    214.7 |    302.6 |    278.4 |
|            26 |    171.7 |    314.9 |    326.0 |
|            34 | **75.8** |    327.9 |    271.6 |

We start at parity and peak on matchday 19. We then decline for fifteen
consecutive matchdays to EUR 75.8m — a **65% drawdown**. This is not an
end-of-season sell-off; the full per-matchday series declines monotonically in
trend from 19 onward. The winners plateau above EUR 300m and stay there.

**Not all of it was destroyed. Most of it was parked.** REH-75 measured
−EUR 55.3m of realised round-trip losses. The rest became idle cash: a live
`status` run on 2026-08-21 reports a budget of **EUR 94,034,410** against a
squad worth EUR 75.8m. Cash scores zero points every matchday.

## 5. What they did differently: direction, not volume

`manager_transfers`, 2026-01-03 → 2026-05-16 — the collapse window:

| manager  | transfers |     bought |       sold |       net cash |
| -------- | --------: | ---------: | ---------: | -------------: |
| ours     |        36 |  EUR 69.8m | EUR 139.6m | **+EUR 69.8m** |
| winner 1 |        38 | EUR 130.2m | EUR 123.2m |      −EUR 7.1m |
| winner 2 |        42 | EUR 226.6m | EUR 203.0m |     −EUR 23.6m |

**Transfer counts are nearly identical.** We did not trade less than the
winners, and we did not trade more. We traded in the opposite direction: a net
liquidator against two net accumulators who recycled roughly twice our gross
volume while staying near cash-neutral.

This retires REH-71's framing directly. The question "should `auto` trade for
profit at all?" assumed our problem was trading too much. At 36 transfers
against their 38 and 42, volume was never the difference.

## 6. The mechanism: the bot cannot pace a season

Visible in one replay run, `--with-competition --with-flip-buys`, from the live
`SmartBidding.calculate_ep_bid` path:

```
bid=71008000  ceiling=80000000   <- first buy: EUR 71m on ONE player
              ceiling=48770064
              ceiling=29103402
              ceiling=4687915    <- and thereafter, all season:
bid=0  ep_gain=+85.8  ask=15678910  ceiling=4687915
bid=0  ep_gain=+81.3  ask=18777777  ceiling=4687915
bid=0  ep_gain=+76.8  ask=43859164  ceiling=4687915
```

The run ends with **5 buys and a final budget of EUR 500,000**. Every declined
candidate above was rated `must_have` by the bot's own tiering. It did not
misjudge them; it had no money left.

`ep_max_bid = budget_ceiling * max_bid_fraction(gain)` asks what fraction of
*current* budget a signing justifies. That is a single-decision question with a
defensible answer. Nothing anywhere asks the sequential question — what share
of a season's capital one player should consume — so a +194.7 gain takes 0.8 of
the ceiling, and the season's remaining `must_have` candidates are unaffordable.

**REH-69 predicted exactly this and fixed only half of it.** Its own comment
reads: *"the bot would commit 80% of budget to the first qualifying candidate
and be unable to afford the one that mattered next week."* REH-69 restored the
*gradient* — a +43 and a +195 are no longer sized identically — but a genuinely
large gain still consumes the budget, because the ramp tops out at 0.8 of
whatever is available rather than at a share of the season.

### What this is not

A quality-threshold problem. Sweeping the buy floor makes things monotonically
worse, so "buy more" is not the fix:

|    `min_ep_gain` |  simulated | vs actual | buys |
| ---------------: | ---------: | --------: | ---: |
| **40 (shipped)** | **26,960** |  **+788** |   21 |
|               30 |     25,319 |      −853 |   25 |
|               20 |     25,646 |      −526 |   29 |
|               10 |     22,955 |    −3,217 |   43 |
|                5 |     23,628 |    −2,544 |   49 |

The constraint was never the bar. Lowering it adds marginal players; the
winners bought expensive good ones. Different lever.

## 7. What remains blocked

Question 2 of the ticket — reconstruct the winners' squads and score them — is
**fully** blocked, not partially. It needs per-transfer counterparty
identification, and the table the ticket assumed provides it does not exist.
`manager_transfers` records each manager's own transfers without linking the
two sides. Scoring a half-reconstructed squad as if complete would be worse
than not answering.

Note that questions 1 and 3, which decide direction, did not need it.

## 8. Caveats

- Replay figures come from a harness whose buy side is an upper bound: only
  players who actually traded are visible, and without `--with-competition`
  every wanted listing is won. The pacing evidence in §6 comes from a
  competition-modelled run, which is the stricter setting.
- REH-71 records a faithfulness decision moving a replay total by 6,162 points.
  Treat any single replay delta smaller than that with suspicion unless it
  replicates. The §6 finding does not rest on a delta — it rests on a logged
  ceiling collapse and a buy count of 5.
- Points-per-value (§3) flatters cheap squads by construction. Direction only.
- `league_rank_history` runs to 2026-08-21 because the table is still being
  written; only rows through matchday 34 of 2025/26 are used here.

## 9. What this implies for the backlog

The queued work is aimed at scoring: REH-52 (availability model), REH-56
(scorer validation), REH-70 (5-season backtest), REH-80 (cold-start prior).
Section 3 says scoring is not the binding constraint, and REH-80 has already
been measured and reverted for costing 782 points.

The binding constraint is capital pacing (§6). Nothing in the backlog addresses
it, and it is the only finding here with a mechanism precise enough to fix.
