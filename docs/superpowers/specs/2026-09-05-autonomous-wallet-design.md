# Rehoboam runs the wallet: one execution path, bids as commitments, a squad plan

Date: 2026-09-05
Status: design, approved in conversation, not yet planned
Related: REH-85 (capital pacing), REH-99/REH-100 (bid ceiling, safety gate on
every buy), REH-102 (high-bidder losses), REH-108 (stale proposals), REH-111
(tier-aware bid evaluation), REH-114 (emergency fill asks before it spends),
REH-115 (manual bids not cancelled), REH-117 (proposal overview), REH-118/119
(33% rule, watch targets)
Supersedes: `2026-08-23-approval-gated-trading-design.md` §1–§4 (what the bot
does alone, what needs approval, the proposal, approval by webhook). Its §5
(safety gate), §6 (daily message) and §7 (failure behaviour) stand.

______________________________________________________________________

## Why

Measured 2026-09-04 against prod telemetry (App Insights, `AppTraces`) and the
Kickbase API tables the bot keeps (`manager_transfers`, `league_transfers`,
`league_rank_history`, `manager_profile_history`).

**The bot runs and decides, and has not bought a player on its own since
2026-08-23.** Every prod session from 2026-08-30 to 2026-09-04 ended
`trades=0/0 errors=0`. That counter only counts autonomous executions
(`auto_trader.py`, `session-end`), and there are none left:

| path           | what it does today                                  | why it never fires                                                                                                                                      |
| -------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| plain EP buy   | `_propose_buy` → Telegram, waits for a tap          | approval gate, 2026-08-24                                                                                                                               |
| emergency fill | `_propose_buy` with a 24h auto-approve deadline     | REH-114, 2026-08-31 — the deadline is checked on a 12h timer, so it fires 24–36h later; El-Faouzi's fired 2026-09-02 08:00 to "no longer on the market" |
| trade pair     | `if available_slots + proposed_slots > 0: continue` | runs only at 15/15; the squad was 7–12/15 all season                                                                                                    |
| profit flip    | evaluated every session                             | every candidate "would make squad unfieldable" or "Cannot afford"                                                                                       |

**The approvals that were tapped mostly did not land.** 13 approvals between
2026-08-29 and 2026-09-02 produced 2 acquisitions (Maloney EUR 1,441,358;
Castello Jr. EUR 28,546,452). The rest:

| approved bid                                        | what happened                                                                                                                                             |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Badé EUR 17,126,503                                 | cancelled by `bid_evaluator` 12h later: "30.0% over market value – too expensive for flip"                                                                |
| Kleindienst EUR 24,748,891                          | cancelled, same reason; Kupka won at EUR 21,548,071                                                                                                       |
| Harder EUR 6,424,255                                | cancelled 2026-09-01 08:00: "34.4% over market value – above its ceiling"; re-placed by hand at 16:06, won, sold next day for EUR 4,589,334               |
| Castello Jr. EUR 20,089,389                         | cancelled 2026-09-01 20:00: "39.4% above its ceiling" — **after** REH-115 deployed; re-proposed the same session at EUR 27,119,389, won at EUR 28,546,452 |
| Orban EUR 40,386,341                                | "HIGH BIDDER and still lost" — winner paid EUR 31,750,000                                                                                                 |
| Coufal EUR 31,536,624                               | "HIGH BIDDER and still lost" — winner paid EUR 26,999,999                                                                                                 |
| Hofmann, Nusa, Ebnoutalib                           | outbid by EUR 2.4m / EUR 650,000 / EUR 2.5m                                                                                                               |
| Aouchiche, Bornauw, Nguefack, one marginal-tier bid | refused by the safety gate at tap time, or `UnderpayNotAllowed` from Kickbase                                                                             |

REH-115 exempts bids placed by hand — those have no tier in `pending_bids`. An
approved proposal is recorded **with** a tier (`notify/approval.py`,
`_record_bid_for_learning(..., tier=...)`), so `bid_evaluator.py` treats it as
the bot's own and re-judges it against the ceiling every session as the
market value drifts (`bot_bids = set(bid_tiers)`; the `our_bid > self._price_ceiling(market_value, tier)` branch). An approval was final for at
most twelve hours.

**Money.** Kickbase's own `transfer_pnl` for this manager is EUR −6,983,764
this season. Team value plus budget went from EUR 180,525,922 (2026-08-21) to
EUR 154,902,319 (2026-09-02). The largest single components are overbid
premiums paid on the day of purchase (Pavlović EUR 6,120,000, Raum EUR
6,500,000, the 2026-09-02 buys about EUR 10m) and the 2026-08-30 sell-off plus
the Harder round trip (EUR −5,422,494 and EUR −1,834,921 realised). The
Pavlović and Raum premiums were the bot's; the sell-off, the Harder round trip
and the 2026-09-02 buys were manual. **Two hands on one wallet with no protocol is what
lost the money: the bot's approved offers were starved by manual buys placed
between approval and resolution**, which is the likeliest mechanism behind the
Coufal "void" (budget was EUR 2,909,838 at the next session).

**Points.** Rank 10 of 14 after MD1 with 590 points against the leader's 1,269.
Pavlović scored 264 (predicted 79) and Raum 151 (predicted 53): the bot's two
expensive picks are 70% of MD1. The money bought points; what it did not buy is
a squad — the season stood at 7/15 on 2026-08-31 and 11/15 with EUR 7,699,172
on 2026-09-04, one unavailability from a −100 slot.

**What winning bids in this league pay.** 81 rival purchases since 2026-08-15,
market value taken within ±3 days from `player_history.db` and the activity
feed: p25 +4.7%, **p50 +14.8%**, p75 +31.1%, p90 +53.4%; 28% paid at most
5% over. The bot's own auction wins paid +18.5% (Pavlović) and +18.6% (Raum) —
league-typical. Shipped ceilings (`config.py`): marginal 8%, solid 15%, strong
25%, must-have 35% — pre-season estimates, never re-measured.

The 2026-08-23 gate design had one justification that still holds: a human
knows things the data cannot (the example was Sabitzer likely leaving the
league). That channel survives here (§4). What does not survive is putting it
in the execution path, because in a 14-manager league with 24h listings the
dominant cost of a buy is latency, not price, and a proposal that waits for a
tap competes against managers who act at once.

## Goals

1. One execution path for every buy, with `services/safety_gate.check_buy` as
   the only thing between a decision and money.
1. A bid the bot placed within its ceiling stays until Kickbase resolves it.
1. The squad is planned as fifteen slots and a floor, not one greedy pick per
   session: never below 13, cover at every position, upgrades only above the
   floor.
1. Marco's judgement enters as information the bot acts on at its next session,
   never as a gate the market waits for.
1. Bid ceilings are derived from what wins in this league and re-derived on a
   documented cadence.
1. Every offer has a recorded outcome, and the daily message reports outcomes,
   not intentions.
1. A four-matchday trial with a one-setting kill switch and a baseline to judge
   it against.

## Non-goals

- **Scoring and availability.** The 3× underprediction on the two best signings
  and the 23% cold-start population are the only things that produce points,
  and they are independent of who holds the wallet. Separate spec.
- **Advisor mode.** Considered and declined: it moves the poach window to
  Marco's phone and changes no outcome.
- **A per-transaction euro cap.** REH-85 measured the champions at one
  EUR 60–65m signing plus about 25 further buys; a cap near the mean would have
  banned both decisive signings.
- **Flip trading.** Off for the trial (§7). Re-enabling it is REH-71's question,
  not this design's.
- **An LLM anywhere.** Unchanged from the prior spec.

## Design

### 1. One execution path

Every buy — plain upgrade, floor/cover fill, fieldability emergency, trade pair —
runs `decide → BuyGate.check → ExecutionService.buy → record pending bid`. The
path exists: trade pairs use it (`_trade_pair_preflight`, then
`self.execution.buy`), and the emergency fill used it before REH-114
(`git show 0d0dc52^:rehoboam/auto_trader.py`). `ExecutionService.buy` already
refuses without a `BuyGate` (REH-100) and already applies the kickoff-lockout
guard (REH-11).

`_propose_buy` becomes `_execute_buy`: the same trend floor
(`_is_too_falling_to_propose`), the same rendered case (`render_proposal` — now
the record of what was done and why), then `ExecutionService.buy`. There is no
`pending` state, no `auto_approve_at`, no `_process_due_auto_approvals`.
`trade_proposals` stays as the table the rendered case is written to; its
statuses reduce to `executed` and `refused` (gate reasons stored in `message`),
written after the fact. `_send_proposal_overview` becomes the session's
"what I did" board, sent when at least one offer was placed or refused.

`session-end` logs `offers=N refused=M` beside `trades=`. The `trades=0/0`
line that could not distinguish a gated bot from a broken one is what made this
week's diagnosis take a query against App Insights instead of a glance.

The Telegram approval webhook stays deployed for §4 and for the existing
Approve/Reject buttons on any proposal rows that predate the change; it gains
nothing new for buys.

### 2. A placed bid is a commitment

`bid_evaluator.evaluate_active_bids` stops recommending `CANCEL` on price. The
ceiling was enforced once, by the gate, at placement against the market value
of that moment; re-checking it later against a drifted value is what withdrew
Badé, Kleindienst, Harder and Castello. The "Placed manually — not the bot's
bid to cancel" branch (REH-115) and the "Priced as {tier} — within its
ceiling" branch collapse into one: a bid with a recorded tier is kept, a bid
without one is kept.

Two cancel reasons remain, both about the player rather than the price:

- the player is no longer listed — nothing to cancel; `resolve_auctions`
  records the outcome (§6);
- the player's status changed to injured or out since placement
  (`player_details.st`, the field the 2026-08-22 lineup override already
  reads). Cancelled with reason `status_changed`, logged and reported.

`league_compliance.resolve_bid_compliance_issues` is untouched. It enforces a
league rule (a bid may not sit below market value) and its outcomes are raise
or, when the gate refuses the raise, cancel — a legality path, not a judgement
path.

### 3. The squad plan

A pure module, `services/squad_plan.py`, tested the way `safety_gate` and
`emergency_basket` are. It answers, for one session: how many players must be
bought before any upgrade, at which positions, with how much money, and who
may be sold.

**Inputs.** Squad with positions and EP, open bids, budget, pending-bid total,
matchday phase, `POSITION_MINIMUMS`, the pacing allowance
(`services/pacing.capital_reserve`), and three settings: `min_squad_size`
(13, existing), new `squad_cover_gk` / `squad_cover_def` / `squad_cover_mid` /
`squad_cover_fw` (2 / 4 / 3 / 2), and `max_debt_pct_of_team_value` (existing).

**Floor.** `Settings.min_squad_size` (13) is enforced for the first time — its
docstring at `config.py` currently reads "NOT YET ENFORCED". `floor_short = max(0, 13 − squad_size − open_bids)`. While `floor_short > 0`, the session's
allowance goes to closing it; no upgrade buy is considered until it is 0.

**Cover.** Inside the 13, a cover composition: GK 2, DEF 4, MID 3, FW 2 (11),
plus the 2 best-EP players regardless of position. `cover_gaps` lists the
positions below their cover count. A floor buy prefers a cover gap; when there
is none, it takes the best EP per euro. This is what the 2026-08-31 squad
lacked: seven players, no same-position replacement anywhere.

**Two regimes, one objective.** When the squad cannot field a legal eleven,
the existing fieldability emergency runs as today — `select_emergency_basket`
with its `total_ep + 100 × players_bought` objective — but executes, in every
phase, because the alternative is −100 per slot at kickoff. When it can field
eleven but is under the floor, the fill runs through `plan_buys` restricted to
cover positions and the floor allowance, in trading phases only; a bench slot
is insurance, not a penalty, and does not justify buying into a locked window.

**Sells.** `sellable` is the set of players whose departure leaves the squad at
or above the floor with cover intact. `run_profit_sell_phase`'s dead-weight
branch, `optimize_and_execute_squad`'s forced budget-recovery sells, and the
`sell` directive (§4) all draw from `sellable` only. Below the floor, nothing
is sold for money; a dead-weight player below the floor is dead weight the
squad keeps until the floor is met.

**Debt.** The aggressive-phase allowance `current_budget + max_debt`
(`_compute_flip_budget`) stays — buying mid-week and selling to recover before
kickoff is Marco's standing preference and the kickoff guard makes the balance
itself non-negative where it matters. It is allowed only when `recoverable = Σ market_value(sellable) ≥ deficit`, where `deficit = max(0, bid − current_budget)`, computed by the plan — so the floor and the debt rule cannot
both be satisfied by liquidating the bench.

**Wiring.** `_build_session_context` computes the plan once and carries it on
`EPSessionContext`. `run_unified_trade_phase` orders its work by the plan:
emergency basket → floor/cover fill → `plan_buys` upgrades → trade pairs (the
`available_slots + proposed_slots > 0` rule becomes `floor_short == 0 and available_slots == 0`, i.e. pairs when the squad is complete). Upgrade
candidates never displace a floor buy for budget.

### 4. Judgement as input, not as a gate

The Telegram webhook accepts text messages as well as button callbacks, with
the same two-secret authentication. The directives, persisted in a new
`directives` table in `bid_learning.db` (`kind`, `player_id`, `player_name`,
`created_at`, `active`, `note`):

| directive          | effect at the next session                                                                                               |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| `block <player>`   | never buy: excluded from every candidate list — upgrades, fill, pairs, basket. Persists until `unblock`.                 |
| `sell <player>`    | sold at the next session's sell phase if in `sellable`; otherwise the reply says why not and the directive stays active. |
| `pause` / `resume` | while paused, a session reconciles, sets the lineup, reports — and places no offer.                                      |
| `watch <player>`   | exists (REH-119); unchanged.                                                                                             |

Player names resolve against the current market and squad by last name;
ambiguity gets a reply listing the matches with ids and does nothing. Every
directive is acknowledged in the same thread with what will happen and when.
This is the Sabitzer channel from the prior spec, at Marco's latency instead
of the market's: the information arrives when he has it, and the bot acts on
it at 08:00 or 20:00 without a listing waiting on the tap.

### 5. Ceilings from the league

A read-only command in the shape of `derive-thresholds`:

```
rehoboam derive-ceilings [--since YYYY-MM-DD] [--window-days 30]
```

It joins `league_transfers` purchases by managers other than us with the
market value nearest the purchase within ±3 days (from `player_history.db`'s
`market_value_cache` series and `market_value_snapshots`), prints the
distribution of winning overbid percentages with `n`, and proposes:

| tier      | proposed ceiling                    | today's sample |
| --------- | ----------------------------------- | -------------: |
| must-have | p75 of winners                      |          31.1% |
| strong    | p50                                 |          14.8% |
| solid     | p25                                 |           4.7% |
| marginal  | unchanged: `overbid_floor_eur` only |              — |

It changes nothing by itself. The values go into `.env` / the Function's app
settings as the existing `overbid_pct_*` fields. Re-run weekly, by hand: a
thin week's sample must not move real-money caps without someone reading `n`.
The command refuses to propose from fewer than 30 winners.

Two things this keeps that REH-99 established: the euro floor at the cheap
end, and the bidder and the gate reading one `BidCeilingPolicy`.

### 6. An honest ledger

`resolve_auctions` records an `outcome` on every `auction_outcomes` row:

| outcome     | evidence                                                                          |
| ----------- | --------------------------------------------------------------------------------- |
| `won`       | player in squad                                                                   |
| `outbid`    | `league_transfers` shows a buyer at a price above ours                            |
| `void`      | a buyer at a price **below** ours — the REH-102 anomaly, now a first-class result |
| `cancelled` | we cancelled, with the reason from §2 or `league_compliance`                      |
| `expired`   | not in squad, not listed, no transfer record within 3 days                        |

A lost bid is recorded as `lost` the session it disappears, as today; a
follow-up pass at each session upgrades it to `outbid` / `void` once the
feed carries the rival's purchase, or to `expired` after 3 days without one.

`void` is the outcome that must never be silent: it means an offer was not
present at resolution — starved budget, a 33%-rule refusal, or something we
have not seen — and each one is reported with the budget at the previous and
next session beside it.

The daily Telegram message (`_send_daily_summary`) reports: offers placed since
the last message with their outcomes; the squad plan — size, floor short, cover
gaps; team value, budget, their sum and its change since yesterday and since
the trial baseline; next kickoff and phase; active directives; gate refusals.
The word "bought" appears only for `won`. `render_daily_summary` is extended,
not replaced; the email path stays best-effort.

### 7. Flips off, kill switch, trial

`enable_flip_buys` defaults to **True** in `config.py` and the Function has no
override, so flips are on in prod today. For the trial, `ENABLE_FLIP_BUYS=false`
is set as an app setting, not only as a default, and the daily message states
the switch's value.

Kill switch: `DRY_RUN=true` on the Function. A dry-run session reconciles,
scores, plans, reports what it would do, and spends nothing — that behaviour
already exists and is what `rehoboam status` exercises.

Trial: MD3 through MD6, measured against this baseline:

| measure                        | baseline (2026-09-04)             |
| ------------------------------ | --------------------------------- |
| acquisitions per offer         | 2 / 13                            |
| offers cancelled by the bot    | 5                                 |
| team value + budget            | EUR 154,902,319 (2026-09-02)      |
| Kickbase `transfer_pnl`        | EUR −6,983,764                    |
| squad at kickoff / empty slots | 11 / 0 (MD3)                      |
| rank / points vs league median | 10 of 14, 590 vs median 702 (MD1) |

Marco's side of the contract for those four matchdays: no market moves in the
app. Anything he knows goes in through §4. A manual move during the trial is
not forbidden — the bot reads it from the transfer feed and re-plans — but it
invalidates the measurement for that matchday.

### 8. Failure behaviour

Unchanged from the prior spec §7: notification failures never block trading or
lineup; a failed trade still reports. Added: a failure to compute the squad
plan falls back to today's behaviour (no floor, no cover) **and reports that it
did**, because a silent fallback here would reintroduce exactly the greedy
session this design removes.

## Verification

- `squad_plan`, outcome classification and ceiling derivation are pure and get
  exhaustive tests, including the floor/debt interaction (recoverable must
  come from above the floor) and the "cannot field eleven" vs "under the
  floor" split.
- Wiring tests assert the single path: every buy site constructs a `BuyGate`
  and reaches `ExecutionService.buy`; no site reaches `api.buy_player`
  otherwise. `test_autonomous_buy_gate.py` is the model.
- The evaluator's tests (`test_tier_aware_bid_evaluation.py`,
  `test_manual_bids_are_not_cancelled.py`) are rewritten to the new contract:
  price never cancels; status change does.
- Tests pinning the gated behaviour — `test_emergency_fill_approval.py`,
  `test_proposal_wiring.py`, `test_approve_all_batch.py`, parts of
  `test_approval_webhook.py` — are replaced, not deleted: the same scenarios
  now assert execution and the after-the-fact record.
- `replay-season` is re-run with the autonomous policy, the floor and the
  proposed ceilings before PR 3 ships; the baseline is re-measured on the same
  commit rather than quoted from memory. A drop is a stop, not a note.
- Live smoke before merge, per the project rule: `rehoboam auto --dry-run`
  against prod state for PRs 1–3, reading the plan and the offers it would
  place.

## Rollout

Five pull requests, in order; each stands alone and is deployable.

1. **Bids are commitments; emergency fill executes** (§2, and the emergency
   half of §1). Smallest change, stops the bleeding. Target: before the first
   trading-phase session after MD3.
1. **Plain buys execute; the ledger** (§1, §6, `offers=` in `session-end`,
   daily message with outcomes). `CLAUDE.md`'s "Approval gate (2026-08-24)"
   paragraph becomes historical in this PR.
1. **The squad plan** (§3, `min_squad_size` enforced, pair rule rewritten,
   replay re-run).
1. **Directives** (§4).
1. **`derive-ceilings`** (§5), then the first re-measurement and the app
   settings it proposes.

Trial clock starts at PR 3's deploy. PRs 4 and 5 land inside the trial; they
change inputs, not the operating model.

## Risks

- **Autonomy amplifies the scorer, and the scorer's demonstrated edge is
  avoiding bankruptcy, not picking players** (REH-68/69 replay). The floor,
  pacing, ceilings and the kill switch bound the damage; they do not create
  edge. The scoring spec is where edge comes from, and this design does not
  pretend otherwise.
- **The ledger depends on the activity feed.** `outbid` and `void` need the
  rival's purchase in `league_transfers`; a feed gap turns them into
  `expired`. The daily message shows the feed's last sync time so a gap is
  visible.
- **Directive name resolution.** A wrong `sell` is an irreversible instant
  sale. Ambiguity does nothing; a single match is echoed back with the
  player's club and market value before it takes effect at the next session,
  which is the window to `unblock`/cancel it.
- **The floor can lock money in a bad bench.** Thirteen players at all times
  means two are usually not starting. That is the cost of never fielding ten;
  the cover rule keeps them at positions where they are worth something.
- **Manual moves during the trial.** Not prevented, only detected. If they
  happen the trial measures nothing, and the design falls back to being a
  faster version of today.
