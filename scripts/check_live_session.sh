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
# Read-only against Azure and against the store: it only queries
# team_value_history, buy_decisions and auction_outcomes in Supabase Postgres,
# the same tables the live session writes to directly. Safe to run any time
# EXCEPT the minutes around 08:00/20:00 UTC when the Function itself writes.
set -uo pipefail
cd "$(dirname "$0")/.."

echo "=== function app ==="
az functionapp show -g rg-rehoboam -n func-rehoboam \
  --query "{state:state}" -o tsv 2>/dev/null || echo "az query failed"
az functionapp config appsettings list -g rg-rehoboam -n func-rehoboam \
  --query "[?name=='DRY_RUN' || name=='AGGRESSIVE'].{n:name,v:value}" -o tsv 2>/dev/null

echo
echo "=== what the session did ==="
uv run python - <<'PY'
import datetime as dt

from rehoboam.store import connect


def one(conn, q, params=(), d=None):
    try:
        r = conn.execute(q, params).fetchone()
        if not r:
            return d
        v = next(iter(r.values()))
        return v if v is not None else d
    except Exception:
        return d


with connect() as conn:
    tv = conn.execute(
        """select snapshot_at, team_value, budget from rehoboam.team_value_history
           order by snapshot_at desc limit 1"""
    ).fetchone()

    n_dec = one(conn, "select count(*) as n from rehoboam.buy_decisions", d=0)
    n_auc = one(conn, "select count(*) as n from rehoboam.auction_outcomes", d=0)
    n_win = one(
        conn,
        "select count(*) as n from rehoboam.auction_outcomes where winning_bid is not null",
        d=0,
    )

    print(f"buy_decisions rows      : {n_dec}   <-- MUST be > 0 after a live session with listings")
    print(f"auction_outcomes rows   : {n_auc}  (winners resolved: {n_win})")
    if tv:
        when = dt.datetime.fromtimestamp(tv["snapshot_at"], dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
        print(f"team value {tv['team_value']:>14,}  budget {tv['budget']:>14,}   as of {when}")
        if tv["budget"] is not None and tv["budget"] < 0:
            print("  *** NEGATIVE BUDGET -- the whole matchday scores ZERO if this holds at kickoff ***")

    print("\nmost recent declines, with reasons:")
    try:
        rows = conn.execute(
            """select player_name, reason, marginal_ep_gain, budget_ceiling
               from rehoboam.buy_decisions order by "timestamp" desc limit 10"""
        ).fetchall()
        for row in rows:
            gain = row["marginal_ep_gain"]
            g = f"{gain:+.1f}" if gain is not None else "  n/a"
            name = str(row["player_name"])[:22]
            print(f"   {name:<22} {row['reason']:<22} ep {g}  ceiling {row['budget_ceiling'] or 0:,}")
        if not rows:
            print("   (none — expected before the market opens, a RED FLAG after it has)")
    except Exception as e:
        print("   buy_decisions unreadable:", e)
PY

echo
echo "=== last session log lines ==="
tail -5 logs/rehoboam.log 2>/dev/null | sed 's/^/   /' || echo "   no local log (the Function logs to Azure)"
