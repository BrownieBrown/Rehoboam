# LLM decision layer: judgment from Claude, execution and safety from the bot

Date: 2026-08-23
Status: design, approved in conversation, not yet planned
Related: REH-52, REH-90, REH-91, REH-92, REH-93

## Why

Across 2026-08-21 to 2026-08-23 the bot produced five wrong decisions. Every one
was caught by Marco looking at a single player and asking why — never by a test,
a review, or the season replay.

| what happened                                                       | what the bot could not see                                             |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| A long-term-injured player named in the starting eleven             | its own `player_details.st` field, which it never read                 |
| Two players with **zero** Bundesliga appearances ranked 1st and 2nd | that their fitted form was 2. Bundesliga                               |
| A Bayern midfielder skipped below the buy floor                     | that his availability rested on a three-month-old bench appearance     |
| A bid on a Hamburg striker while his value fell 34%/14d             | that Hamburg are newly promoted, and that falling value means anything |
| Sabitzer rated the squad's second-best player                       | that he is likely leaving the league                                   |

The last row is the important one. **No amount of model repair reaches it.** The
information was never in the data — it was in Marco's head, and in the football
press.

The pattern underneath: **every guard the bot has is a threshold on a number the
bot itself computed.** When the number is wrong, the guard is wrong in the same
direction and stays silent. Nothing checks the system from outside its own model.

The audit in REH-93 makes the scale concrete — `score_player_v2` hardcodes 12
values, of which exactly one is a genuine replacement. Eleven capabilities were
lost silently, producing six inert code paths that ruff, mypy, the tests and the
replay all consider healthy.

## Goals

1. Decisions are made with information the fitted model structurally cannot hold:
   injury news, transfer rumours, expected lineups, league context.
1. The bot runs unattended. Marco checks in monthly, not daily.
1. Every decision carries a written reason, retained and readable weeks later.
1. Nothing the LLM decides can breach a limit that the code enforces.
1. A failure of the LLM path degrades to today's behaviour, not to nothing.

## Non-goals

- **Replacing the v2 scorer.** It stays and produces the EP the LLM reasons
  over. This replaces the *decision layer* — `recommend_buys`,
  `build_trade_pairs`, lineup selection — not the scoring.
- **Microservices or Kubernetes.** Considered and rejected: the workload is two
  minutes a day, AKS would cost €30–70/month against a currently-free
  deployment, one person maintains this, and *not one* of the five failures above
  was a coupling problem. Splitting them across network boundaries buries the
  same bugs a layer deeper. The existing two-function-app split stands, because
  it was drawn on a real boundary (different schedule).
- **Human approval per decision.** Explicitly rejected — anything requiring
  approval keeps Marco in the daily loop, which is the problem being solved.

## Design

### 1. Flow

The Azure Function timer stays the entry point. `AutoTrader.run_full_session`
gains one decision step:

1. Fetch squad, market, bids, budget, matchday phase; run the existing v2 scorer.
1. Build a payload (below).
1. **One Claude call** with web search enabled, returning a schema-validated plan.
1. Validate the plan against the hard limits.
1. Execute what survives validation.
1. Email a summary.

### 2. The payload

For every squad and market player: `player_id`, name, club, position, price,
market value, **EP from the v2 scorer**, and the raw facts underneath it —
Kickbase average points, injury status code, 7/14/30-day market-value trend,
whether a fitted quality exists or the position prior was used.

EP and the raw facts are both included deliberately. Every failure above was a
case where EP was wrong and the raw facts were right; the LLM must be able to
disagree with the number.

Plus: budget, committed slots, free slots, reserved flip slots, days to kickoff,
open bids, and the players owned by opponents (`competitor_player_ids`, already
computed each session and currently used for nothing).

### 3. The plan

Structured output, schema enforced by the SDK (`output_config.format`):

```
lineup:     [11 player_ids]
sells:      [{player_id, reason}]
buys:       [{player_id, max_bid, reason}]
flip_buys:  [{player_id, max_bid, reason}]
reasoning:  str
concerns:   [str]
```

Every item carries a reason. The whole plan is persisted per run so a decision
is explicable weeks later — the property whose absence made this week expensive.

### 4. Resource split

**Two squad slots are ringfenced for profit trading**, and the LLM cannot use
them. The remaining slots are the LLM's. The division is fixed rather than
negotiated per run, so neither half can starve the other and each can be
evaluated alone.

**Scope boundary:** this spec covers the LLM decision layer *only*. The
profit-trading half is separate work and does not yet exist in the form intended
— `profit_trader.py` is present but its buy path is disabled (`ENABLE_FLIP_BUYS`
was set false on 2026-08-22 pending REH-71), and it does not implement the
market-value-plus-1% discipline the measured edge depends on. **Until that work
lands, the two reserved slots simply go unused**, and the LLM operates with two
fewer slots than the squad cap. That is deliberate: it keeps this plan focused
and lets the flip half be measured on its own.

For the record, the edge that motivates the reservation: on 161,492 daily market
values across 531 players, buying at `market_value × 1.01` — the league's minimum
legal bid — and holding three days returned **+3.7% net of toll at an 82% win
rate** for players rising >10%/7d in the €5–15M band, while flat-or-falling
players lost ~2.4%. The historical −€55.3M across 151 flips is explained by the
entry premium: at the +12.2% the bot used to pay, that same +3.7% becomes −9.8%.

### 5. The safety gate

A pure function, run before anything executes. It rejects the plan, or the
offending item, and logs why:

- Lineup is exactly 11 and forms a legal formation
- **No player with injury status 4 or 256 appears in the lineup**
- Budget ≥ 0 at kickoff after all buys — a negative balance scores zero for the
  entire matchday
- Spend per run ≤ `Settings.llm_max_spend_per_run`
- Trades per run ≤ `Settings.llm_max_trades_per_run`
- Every bid ≤ market value × (1 + `Settings.max_overbid_pct`)
- No sell that leaves fewer than 11 fieldable players
- Flip buys confined to their two slots
- **Every `player_id` appears in the payload we sent.** A model will occasionally
  invent an identifier; an invented one must never reach `api.buy_player`.

All limits are `Settings` fields, tunable from `.env` without a deploy, matching
the convention the rest of the project uses.

Rejections are counted and reported in the email. A model repeatedly hitting a
limit is a signal, not something to clip silently — that silence is exactly what
let eleven dropped capabilities survive.

### 6. Fail-soft

If the Claude call errors, times out, or returns a plan that fails validation:
**set the lineup using the existing pipeline, execute no trades, and email the
failure.** A bad LLM day is no worse than an ordinary bot day. This is the
isolation property that motivated the microservices question, obtained with a
`try/except` rather than a cluster.

### 7. Email summary

There is currently **no notification channel of any kind** — logs go to a
rotating file and blob storage, and nothing reaches Marco unless he asks. Monthly
check-ins are impossible without one, so this is load-bearing rather than a nicety.

Each run emails: the plan and its reasoning, what executed, what the gate
rejected and why, current squad and budget, and the LLM's `concerns` list.

SMTP credentials follow the existing secret pattern — Key Vault references
surfaced as app settings, exactly as `KICKBASE_PASSWORD` already is. Nothing
lands in the repository.

### 8. Model and cost

`claude-opus-5` with adaptive thinking and the web search server tool.

Roughly 30K input and 10K output per run is about **$0.40**, so twice daily is
**~$25/month** plus web search usage. Trivial against a €95M budget and a season.

## Verification

The safety gate is a pure function and gets full unit coverage — it is where the
risk actually lives. The Claude call is mocked in tests.

**`replay-season` cannot measure any of this.** LLM decisions are not
reproducible, so the harness that gates every scoring change cannot gate this
one. That is a real loss and it is stated here so it is not discovered later.
The mitigation is that the plan is a persisted artifact: decisions can be read
and judged after the fact even though they cannot be replayed.

Worth weighing against that loss: the replay was unchanged across all five
failures above **and** all five of their fixes. It is already blind to most of
what decides matchdays.

## Risks

- **It acts on a rumour.** Web search can surface a false report. Mitigated by
  the safety gate bounding the damage, by reasoning being logged, and by the
  email making it visible within one run.
- **Hallucinated identifiers.** Handled explicitly in the gate.
- **Silent quality drift.** Model behaviour can change without the code
  changing. The persisted plans are the only defence; they should be reviewed
  periodically rather than trusted indefinitely.
- **Cost drift** if the payload grows unattended. Bounded by keeping the payload
  to the squad plus buyable listings, which is a few thousand tokens.
- **It inherits every bad input.** If the v2 scorer says a player is worth 89 EP,
  the LLM sees 89 EP. The raw facts alongside it are the counterweight, but they
  are a counterweight, not a guarantee. REH-90 through REH-93 remain worth doing.

## Decided during review

**Cadence: twice daily**, on the existing 08:00/20:00 UTC timer. No scheduler
change, and it reacts to team news within twelve hours of kickoff. ~$25/month.
Halving to once daily is an `.env` change if the cost ever matters.

**The LLM picks eleven players; `select_best_eleven` arranges the formation.**
Formation is a solved mechanical problem with existing tested code, and asking
the model to also satisfy formation constraints adds a class of invalid plan the
gate would have to reject. Selection is the judgment; arrangement is arithmetic.

## Open questions

1. Whether `concerns` should ever escalate — an item that halts trading until
   Marco replies. Deferred: it reintroduces the approval loop this design exists
   to remove. Revisit only if the emails show a recurring case where autonomous
   action was clearly wrong and a pause would have helped.
