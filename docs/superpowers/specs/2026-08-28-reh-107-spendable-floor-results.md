# The reserve must leave one move spendable (REH-107): measured results

Date: 2026-08-28
Ticket: https://linear.app/jovily/issue/REH-107
Follows: REH-85 (the reserve), REH-101 (bounding it by budget)
Prior results: `docs/superpowers/specs/2026-08-26-reh-85-capital-pacing-results.md`

## Verdict up front

Ship at `pacing_min_spendable_moves=1.0`. It is **exactly neutral in the
replay** (25,000 both sides) and repairs the live position, which is the
regime the replay does not cover. A larger floor measures +309 points, which
is below this instrument's noise floor and has no principled justification —
recorded below, not shipped.

## What was wrong

REH-101 bounded the reserve by `budget * max_reserve_fraction`. Necessary — an
unbounded reserve demanded more than the wallet held and refused every buy.
But the bounded figure stops representing a whole number of moves. Live on
2026-08-28:

```
reserve = min(2 moves x EUR 12.5m, 0.5 x EUR 21,720,227) = EUR 10,860,113
```

EUR 10.86m held back to fund "2 further moves" at a EUR 12.5m median — **0.87
of one move**. It bought nothing later while blocking every upgrade above it.
The session placed `bid=0` on the five highest-EP players on the board;
Engelhardt (+45.0 EP) missed the cap by EUR 134,301.

## The fix, and the shape it is deliberately NOT

A third bound: `reserve <= budget - min_spendable_moves * median_move`.
Whatever else it protects, the reserve must leave one typical move affordable.

The ticket originally proposed flooring to whole moves,
`floor(affordable / median) * median`. **That was wrong and was not
implemented.** It restores the "whole number of moves" meaning but makes
headroom a sawtooth in budget: crossing a move boundary drops the maximum bid
by a full median, so gaining budget can shrink what the bot may bid. That is
precisely the pathology REH-101 exists to prevent, and it is pinned by
`TestRaisingCashIsStillNeverPunished` — a monotonicity sweep from EUR 5m to
EUR 60m that the sawtooth form fails.

## Where this fix operates

At `max_reserve_fraction=0.5` the new bound binds only when
`budget < 2 x median_move`. That is computable in advance, and it says the
replay cannot see the shipped default:

| position             |    budget |    median | binds below | binds?  |
| -------------------- | --------: | --------: | ----------: | ------- |
| replay, season start |  ~EUR 80m |  EUR 6.6m |   EUR 13.2m | no      |
| live, 2026-08-28     | EUR 21.7m | EUR 12.5m |   EUR 25.0m | **yes** |

The null replay result below was therefore **predicted before the sweep ran**,
not discovered in it. Same discipline as REH-98 and REH-105.

## The sweep

All runs `uv run rehoboam replay-season --with-competition`, same local
`logs/training_corpus.db` / `logs/bid_learning.db`, matching REH-85's method.

| configuration                 |     points | vs main | buys | finish   |
| ----------------------------- | ---------: | ------: | ---: | -------- |
| `--no-pacing`                 |     24,141 |    -859 |    3 | 11 of 14 |
| `0.0` (= REH-101, main today) |     25,000 |       — |    3 | 10 of 14 |
| **`1.0` (shipped default)**   | **25,000** |   **0** |    3 | 10 of 14 |
| `2.0`                         |     25,309 |    +309 |    3 | 10 of 14 |
| `3.0`                         |     25,309 |    +309 |    3 | 10 of 14 |
| `5.0`                         |     25,309 |    +309 |    3 | 10 of 14 |
| `8.0`                         |     25,309 |    +309 |    3 | 10 of 14 |

Reading:

- **The shipped default is a pure no-op here**, as predicted. The replay is a
  regression check for this change, not a demonstration of it.
- The +309 at 2.0 saturates immediately and identically through 8.0, i.e. the
  reserve is already fully relieved by 2.0 wherever it mattered. It is not a
  gradient anyone is climbing.
- +309 is 1.2%. REH-71 records a single faithfulness decision worth 6,162
  points on this instrument; a delta twenty times smaller is not a signal.
- Buy count is 3 in every row. REH-85's own acceptance criterion — buy count
  must rise — still fails, for the reason documented there: this replay never
  reaches 15/15, and the constraint at season start is the fraction, not the
  floor. REH-107 does not claim to fix that.

## Live verification

`rehoboam status`, 2026-08-28, against the real market:

```
pacing session median_move=12500000 slots_to_fill=3 reserve=9220227
               budget=21720227 max_fraction=0.50 min_spendable_moves=1.00
```

Reserve EUR 10,860,113 -> EUR 9,220,227, so `pace_cap` becomes exactly one
median move:

| player      | EP gain |        ask | before            | after                 |
| ----------- | ------: | ---------: | ----------------- | --------------------- |
| Jóhannesson |   +51.1 | 10,008,196 | capped 10,860,114 | 11,848,196 (uncapped) |
| Engelhardt  |   +35.3 | 10,994,415 | **bid=0**         | **12,394,415**        |
| Reitz       |   +34.3 | 13,087,545 | bid=0             | bid=0                 |
| Veerman     |   +33.0 | 18,543,633 | bid=0             | bid=0                 |

Reitz and above stay refused, correctly: they cost more than one typical move
against this budget, which is the rule doing its job rather than failing.

## Open

`min_spendable_moves` is a `Settings` field and a `replay-season` flag, so
re-tuning needs no deploy. Worth revisiting on an instrument that reaches
15/15 — REH-88 (replay is blind to trade pairs) and REH-68 (full-fidelity
replay) are the blockers on measuring the reserve properly.
