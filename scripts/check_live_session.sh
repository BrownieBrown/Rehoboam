#!/usr/bin/env bash
# Post-session health check for a live trading run (REH-86 era).
#
# Answers the three questions that matter after a session, in the order that
# they hurt if the answer is wrong:
#   1. Is the budget >= 0?  A negative budget at kickoff zeroes the ENTIRE
#      matchday -- worse than any bad trade.
#   2. Did the bot record its decisions?  `buy_decisions` had never held a row
#      in production before 2026-08-21, and the wiring could not be smoke-tested
#      pre-season because an empty market never reaches the branch.
#   3. Did the session finish cleanly?
#
# Read-only against Azure except for `fetch-azure-state`, which pulls the prod
# blobs into ./logs (preserving local work as .local-bak). Safe to run any time
# EXCEPT the minutes around 08:00/20:00 UTC when the Function itself writes.
set -uo pipefail
cd "$(dirname "$0")/.."

echo "=== function app ==="
az functionapp show -g rg-rehoboam -n func-rehoboam \
  --query "{state:state}" -o tsv 2>/dev/null || echo "az query failed"
az functionapp config appsettings list -g rg-rehoboam -n func-rehoboam \
  --query "[?name=='DRY_RUN' || name=='AGGRESSIVE'].{n:name,v:value}" -o tsv 2>/dev/null

echo
echo "=== pulling prod state ==="
uv run rehoboam fetch-azure-state 2>&1 | tail -3

echo
echo "=== what the session did ==="
uv run python - <<'PY'
import sqlite3, datetime as dt
c = sqlite3.connect("file:logs/bid_learning.db?mode=ro", uri=True)

def one(q, d=None):
    try:
        r = c.execute(q).fetchone()
        return r[0] if r and r[0] is not None else d
    except Exception:
        return d

tv = c.execute("""select snapshot_at, team_value, budget from team_value_history
                  order by snapshot_at desc limit 1""").fetchone() \
     if one("select count(*) from sqlite_master where name='team_value_history'") else None

n_dec = one("select count(*) from buy_decisions", 0)
n_auc = one("select count(*) from auction_outcomes", 0)
n_win = one("select count(*) from auction_outcomes where winning_bid is not null", 0)

print(f"buy_decisions rows      : {n_dec}   <-- MUST be > 0 after a live session with listings")
print(f"auction_outcomes rows   : {n_auc}  (winners resolved: {n_win})")
if tv:
    when = dt.datetime.fromtimestamp(tv[0], dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    print(f"team value {tv[1]:>14,}  budget {tv[2]:>14,}   as of {when}")
    if tv[2] is not None and tv[2] < 0:
        print("  *** NEGATIVE BUDGET -- the whole matchday scores ZERO if this holds at kickoff ***")

print("\nmost recent declines, with reasons:")
try:
    rows = list(c.execute("""select player_name, reason, marginal_ep_gain, budget_ceiling
                             from buy_decisions order by timestamp desc limit 10"""))
    for n, why, gain, ceil in rows:
        g = f"{gain:+.1f}" if gain is not None else "  n/a"
        print(f"   {str(n)[:22]:<22} {why:<22} ep {g}  ceiling {ceil or 0:,}")
    if not rows:
        print("   (none — expected before the market opens, a RED FLAG after it has)")
except Exception as e:
    print("   buy_decisions unreadable:", e)
PY

echo
echo "=== last session log lines ==="
tail -5 logs/rehoboam.log 2>/dev/null | sed 's/^/   /' || echo "   no local log (the Function logs to Azure)"
