# ADR-0014 — The clock: sessions after the nightly update, a backstop before each kickoff

**Status:** Proposed, 2026-11-18 (Marco: "why not an extra friday run we can move the timings in
general if there are better ones") · **Amends:** nothing; makes ADR-0003's "the last session
before the kickoff" a property of one schedule instead of a 12-hour assumption

## Context

The bot ran at 08:00 and 20:00 UTC since REH-48. Market values move once a night, at ~22:00
Berlin (21:00 UTC in winter, 20:00 in summer), so the evening session ran an hour *before* the
move in winter: a bid on a listing that expires overnight had to forecast tonight's value, and
Kickbase declines any bid below the value at expiry (the 2026-10-07 break review); the turn and
the rise of the night were first seen ten hours later, at 08:00.

The repayment moved to the last session before the kickoff on 2026-11-18 (PR #158), which made
that one session a single point of failure: if Friday 08:00 fails, the wallet is negative at the
kickoff and the matchday scores zero.

The times lived in three places: the cron string, `sim.engine.SESSION_TIMES_UTC` and a 12-hour
gap in `services/repay_window`.

## Decision

1. **Regular sessions at 08:00 and 22:00 UTC**, the evening one after the nightly update.
1. **One extra session `BACKSTOP_LEAD` (4 h) before each round's first kickoff**, from an hourly
   tick that reads the kickoff the last session stored (`SessionStore.latest_next_kickoff`) and
   returns unless `desk.schedule.backstop_due` says this is the tick. It is not a regular
   session: the regular session before the lock still repays, and the backstop repays only what
   is left (`repayment_due` asks for the next *regular* session), and sets the lineup on the
   latest team news.
1. **One home:** `rehoboam/desk/schedule.py`. The replay reads `SESSION_TIMES_UTC` from it, the
   repayment rule reads `next_session` from it, and a test pins both Azure cron strings to it.

## Evidence (leader scorecard, ADR-0012; 15 paths each, store as of 2026-11-18)

| configuration                   | W1 pts/MD | W1 total | day-0 pts/MD | day-0 total | empty |
| ------------------------------- | --------- | -------- | ------------ | ----------- | ----- |
| frozen-6, 08:00 + 20:00         | 928       | +30.0m   | 908          | +4.5m       | 0     |
| **frozen-6, 08:00 + 22:00**     | **950**   | +31.7m   | **940**      | **+14.4m**  | 0     |
| frozen-6, 08:00 + 15:00 + 20:00 | 920       | +30.1m   | 940          | +6.5m       | 0     |

08:00 + 22:00 is better on every axis in both windows and passes ADR-0012's keep rule against
08:00 + 20:00. A third *trading* session a day traded worse on W1, so the extra run trades
nothing new: it is a backstop. The backstop itself is not in the replay (a replay session never
fails); it changes nothing when the regular session has repaid.

## Consequences

- Every scorecard on main now runs on 08:00 + 22:00. Frozen tags keep their own clock (they are
  scored from a worktree of the tag).
- `engine.decide` still asks whether the kickoff is within `SESSION_GAP` (12 h) for its own
  repayment. That agrees with `next_session` for every Bundesliga kickoff time (all before 20:00
  UTC) and should move to `next_session` with the next v3 change that is scored anyway.
- 24 timer invocations a day instead of 2; 23 of them read one row and return.
- Live verification: the first Friday shows a `(backstop)` session in App Insights 4 h before the
  kickoff, and the 22:00 session's facts row the evening before.
