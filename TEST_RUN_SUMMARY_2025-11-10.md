# Test Run Summary - November 10, 2025

## Current Situation

**Your Active Bids**:

- Dominik Kohr: €9,281,213
- Robin Hack: €7,226,114

**Budget**:

- Current Budget: €18,199,803
- Pending Bids: €16,507,327
- **Effective Budget: €1,692,476** ⚠️ (very limited!)

**Team Value**: €156,176,030
**Max Debt Allowed**: €93,705,618 (60%)
**Available for Flips**: €88,479,016

## What the Bot Found

### Profit Trading Opportunities (10 found)

**Top Opportunities**:

1. **Omar Traoré** (Defender)

   - Price: €3,946,014
   - Smart Bid: €4,340,615 (+10%)
   - Value Score: **100.0** 🔥
   - Average: 69.4 pts/game
   - Status: **Too expensive** for effective budget

1. **Woo-Yeong Jeong** (Midfielder)

   - Price: €2,829,974
   - Smart Bid: €3,112,971 (+10%)
   - Value Score: **95.0**
   - Average: 33.2 pts/game
   - Status: **Too expensive** for effective budget

1. **Dominik Kohr** (Defender) ⚠️ **YOU ALREADY BID**

   - Price: €8,554,114
   - Smart Bid: €9,409,525 (+10%)
   - Your current bid: €9,281,213
   - Value Score: **90.0**
   - Average: 63.1 pts/game
   - Status: Bot would skip (already bid €9.2M, new bid not 5% higher)

1. **Lennard Maloney** (Midfielder)

   - Price: €2,114,189
   - Smart Bid: €2,325,608 (+10%)
   - Value Score: **90.0**
   - Status: **Too expensive** for effective budget

1. **Robin Hack** ⚠️ **YOU ALREADY BID**

   - Your bid: €7,226,114
   - Status: Bot correctly skipped

**What Bot Actually Did** (with €1.69M effective budget):

- ✅ Bought: **David Nemeth** - €500,000 (20% expected appreciation, 91.9% below peak)
- ✅ Bought: **Elias Baum** - €500,000 (20% expected appreciation, 88.3% below peak)

**Total Profit Spend**: €1,000,000

### Lineup Improvement Opportunities (16,756 found!)

**Best Trade Found**:

- Strategy: Includes Dominik Kohr + Robin Hack
- Required Budget: €16,625,265
- Status: **Cannot afford** (need €16.6M, only have €1.69M effective)

**Why Bot Can't Execute Lineup Trades**:

- All good trades need €10M-€20M
- Your €16.5M is tied up in pending bids
- Effective budget only €1.69M

## Analysis

### The Problem: Budget Locked Up

Your 2 active bids are locking up **90.7%** of your budget:

```
€18.2M budget - €16.5M pending = €1.7M effective
```

This severely limits what the bot can do:

- ❌ Can't bid on good opportunities (Omar €4.3M, Jeong €3.1M)
- ❌ Can't execute lineup improvements (need €10M+)
- ✅ Can only bid on cheap players (€500K each)

### What Happens if Your Bids Win?

**If you win Kohr (€9.2M) and Hack (€7.2M)**:

- Budget drops to: €18.2M - €16.5M = **€1.7M remaining**
- Very limited for next trades

**If you win Kohr but lose Hack**:

- Budget: €18.2M - €9.2M = **€9.0M remaining**
- Much better! Can bid on Jeong, Maloney, etc.

**If you lose both**:

- Budget: **€18.2M remaining** (full budget back!)
- Can execute all recommended trades

### Strategy Implications

**Current Bids**:

- Kohr (€9.2M): Value score 90, good defender (63 pts/game)
- Hack (€7.2M): Value score unknown from test

**Question**: Are these flips or long-term holds?

- If **flips**: Good value, wait for 10%+ profit
- If **long-term**: Good players, could improve starting 11

**Bot's Behavior** (if running automated):

1. Wait to see if bids win
1. If budget frees up, bid on Omar (€4.3M), Jeong (€3.1M)
1. Execute cheap flips meanwhile (Nemeth, Baum at €500K each)
1. Check lineup trades when budget allows

## Recommendations

### Option 1: Let Current Bids Play Out

- Wait to see if you win Kohr/Hack
- Bot will trade with whatever budget remains
- Safe, but limits this week's opportunities

### Option 2: Cancel One Bid to Free Budget

- Keep Kohr (better value score 90)
- Cancel Hack bid → frees up €7.2M
- Bot can then bid on Omar, Jeong, Maloney
- More aggressive, more opportunities

### Option 3: Wait for Auction to End, Then Go Aggressive

- If you lose both → €18.2M available
- Bot can execute all profit trades (Omar, Jeong, etc.)
- Bot can execute lineup improvements (€10M-€20M trades)
- If you win both → very limited, need to sell/flip to free budget

## Bot Performance This Week (Dry Run)

**What Bot Would Do**:

1. ✅ Recognize your 2 active bids
1. ✅ Calculate effective budget correctly (€1.69M)
1. ✅ Skip Kohr/Hack (already bid)
1. ✅ Find affordable opportunities (Nemeth, Baum)
1. ❌ Can't execute big trades (budget locked)

**Profit Trades**: 2/2 executed (€1M spent)
**Lineup Trades**: 0/0 executed (couldn't afford)

## Next Steps

**If running bot this week**:

```bash
# Conservative (current budget limitations)
rehoboam daemon --interval 180 --max-trades 2 --max-spend 2000000

# After bids resolve (if budget frees up)
rehoboam daemon --interval 180 --max-trades 3 --max-spend 30000000
```

**Monitor**:

- Check when Kohr/Hack auctions end
- Budget will free up when resolved
- Bot will then see more opportunities

**Key Insight**: The bot is working correctly but is limited by your pending bids. This is expected behavior - it's protecting your budget exposure!
