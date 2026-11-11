# Architecture decision records

One file per decision that shapes the bot, in Michael Nygard's format: context, decision,
consequences. An ADR is never edited to change its decision. A new ADR supersedes it, and the
old one's status says so. Rules and constants change by amending the spec or an ADR, with a
backtest run attached (spec `2026-11-11-rehoboam-v3-design.md` §9). A dated bullet in CLAUDE.md
does not change a rule.

| #    | Decision                                                       | Status   |
| ---- | -------------------------------------------------------------- | -------- |
| 0001 | Trade for drift with a full squad                              | Accepted |
| 0002 | Value every player every session; no stored labels             | Accepted |
| 0003 | One planner: a level-triggered reconcile loop                  | Accepted |
| 0004 | The premium is a cost, capped at the band's median             | Accepted |
| 0005 | No live trading without a money-and-points verdict             | Accepted |
| 0006 | Keep the foundation, rebuild the decision layer, live at reset | Accepted |
