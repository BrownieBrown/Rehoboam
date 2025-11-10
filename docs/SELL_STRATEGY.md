# Aggressive Sell Strategy

## Overview

The bot now uses an **aggressive sell strategy** focused on maximizing team value, not protecting your current lineup.

## Key Changes

### Before (Conservative)

```
❌ Won't sell starters (even if they peaked)
❌ Won't sell high performers (even if declining)
❌ Won't sell if squad at minimum size
```

### After (Aggressive)

```
✅ Recommends sales based on value analysis
✅ Ignores lineup position (starter vs bench)
✅ Only enforces squad size within 2 days of match
```

## How It Works

### 1. Value-Based Recommendations

The bot analyzes **all players** using the same criteria:

- **Peaked and declining** (highest priority)
- **Profit target reached** (≥15%)
- **Stop-loss triggered** (≤-15%)
- **Underperforming** (value score \<30)
- **Difficult schedule ahead** (SOS)
- **Falling trend + at profit** (>5%)

**No special protection** for:

- Starting eleven players
- High scorers
- Squad size (except near match day)

### 2. Match Day Awareness

Bot fetches **next match date** from API:

```
GET /v4/leagues/{leagueId}/teamcenter/myeleven
```

**Squad size enforcement:**

- **>2 days until match**: Recommend any sell that improves value
- **≤2 days until match**: Only enforce minimum squad size (10 players)

### 3. Example Scenarios

#### Scenario 1: 5 days until match, 10 players

```
Player: Danel Sinani
Purchase: €6M → Peak: €17M → Current: €14M
Status: Peaked and declining -17.6%

Recommendation: SELL ✅
Reason: Peaked and declining, even though you only have 10 players
```

#### Scenario 2: 1 day until match, 10 players

```
Player: Danel Sinani
Purchase: €6M → Peak: €17M → Current: €14M
Status: Peaked and declining -17.6%

Recommendation: HOLD ⚠️
Reason: Match tomorrow - need 10 players minimum
```

#### Scenario 3: 1 day until match, 15 players

```
Player: Danel Sinani
Purchase: €6M → Peak: €17M → Current: €14M
Status: Peaked and declining -17.6%

Recommendation: SELL ✅
Reason: Peaked and declining, safe to sell (15 players)
```

## Philosophy

### Old Approach: Protect Current Team

- Keep starters at all costs
- Keep high scorers
- Maintain large squad
- **Result**: Miss selling windows, hold declining players

### New Approach: Maximize Value

- Sell peaked players immediately
- Take profits when available
- Continuously improve roster
- Only enforce minimums near match day
- **Result**: Always sell at peaks, buy better replacements

## Console Output

### Match Day Timing

```bash
$ rehoboam analyze

Next match in 5 days (2024-02-15)
You have 10 players in squad
Squad size ok - bot will recommend sales to improve team
```

### Near Match Day

```bash
$ rehoboam analyze

Next match in 1 days (2024-02-10)
⚠️  Match in 1 days! Squad at minimum size (10/10)
```

### Squad Analysis Table

```bash
📊 Your Squad Analysis
────────────────────────────────────────────────────────────────
Player          Purchase    Current     Peak         Profit    Recommendation
────────────────────────────────────────────────────────────────
Danel Sinani    €6,000,000  €14,000,000 €17,000,000  +133.3%  SELL
                                        -17.6%
                Reason: Peaked and declining -17.6% over 11d

Florian Wirtz   €15,000,000 €20,000,000 €20,000,000  +33.3%   HOLD
                                        at peak
                Reason: At peak value - wait for more gains

... (all players)
────────────────────────────────────────────────────────────────

📋 Recommendations: 3 SELL, 7 HOLD

⚠️  Found 3 player(s) you should consider selling!
```

## Workflow

### Daily Analysis (5+ days until match)

1. Run `rehoboam analyze`
1. **Sell all SELL recommendations** (even starters!)
1. Use freed budget to buy better players
1. Repeat daily

### Pre-Match Day (2-3 days until match)

1. Run `rehoboam analyze`
1. Check squad size (need ≥10 players)
1. If \<10 players: Buy first, then sell
1. If ≥10 players: Sell normally

### Match Day (1 day until match)

1. Run `rehoboam analyze`
1. **Only sell if squad >10 players**
1. Focus on buying to fill starting eleven
1. Set your lineup

### Post-Match Day

1. Wait for market values to update
1. Check for peaked players
1. Sell any players who peaked during match
1. Restart daily cycle

## Benefits

### ✅ Never Miss Selling Windows

- Sinani peaked at €17M → Bot recommends SELL immediately
- Don't wait until value crashes to €10M
- **Profit**: €11M instead of €4M

### ✅ Continuous Team Improvement

- Sell declining players → Buy rising stars
- Always have budget for good deals
- **Result**: Higher team value over time

### ✅ No Emotional Attachment

- Starter? Doesn't matter if they peaked
- 100 points? Doesn't matter if declining
- **Focus**: Pure value analysis

### ✅ Match Day Safety

- Still enforces minimums near match day
- Won't leave you unable to field a team
- **Balance**: Aggressive + Safe

## Configuration

### Minimum Squad Size (Match Day)

```python
# .env or config
MIN_SQUAD_SIZE = 10  # Default: 10 (required for Bundesliga)
```

### Deprecated Settings (No Longer Used)

```python
NEVER_SELL_STARTERS = False  # Ignored - bot always recommends based on value
ALLOW_STARTER_UPGRADES = True  # Ignored - bot always allows upgrades
MIN_POINTS_TO_KEEP = 50  # Ignored - bot uses value analysis
```

## Testing

### Test Next Match Date Detection

```bash
python test_next_match.py
```

### Expected Output

```
TESTING NEXT MATCH DATE DETECTION
══════════════════════════════════════════════════════════════════

1. Logging in...
✓ Logged in successfully

2. Fetching leagues...
✓ Using league: My League

3. Fetching starting eleven...

RAW API RESPONSE
══════════════════════════════════════════════════════════════════
{
  "nm": 1707516000,  // Next match timestamp
  "lp": [...],       // Lineup players
  ...
}

NEXT MATCH DATE DETECTION
══════════════════════════════════════════════════════════════════
✓ Found potential match date field: 'nm'
   Parsed as timestamp: 2024-02-10 10:00
   Days until match: 5
```

## Example: Sinani Selling Decision

### Your Situation

- **Bought**: €6,000,000 (30 days ago)
- **Peak**: €17,000,000 (11 days ago)
- **Current**: €14,000,000 (today)
- **Decline**: -17.6% from peak
- **Squad Size**: 10 players
- **Next Match**: 5 days

### Bot Analysis

```python
# Value Analysis
profit_pct = (14M - 6M) / 6M * 100 = +133.3%  ✅ Great profit
peak_decline = (14M - 17M) / 17M * 100 = -17.6%  ⚠️ Declining
days_since_peak = 11 days  ⚠️ Trending down

# Sell Signal: PEAKED AND DECLINING (priority!)
recommendation = "SELL"
confidence = 0.9
reason = "Peaked and declining -17.6% over 11d"

# Protection Check
days_until_match = 5  # >2 days
enforce_squad_size = False  # Not enforcing

# Final Recommendation
→ SELL (no overrides)
```

### What You Should Do

1. **List Sinani for sale** at €14M (or slightly below for quick sale)
1. **Use the €14M** to buy a rising player (value score >60)
1. **Profit locked**: €8M gain from Sinani
1. **New player**: Better potential for future gains

### If You Wait

- Sinani continues declining: €14M → €12M → €10M
- Lost opportunity: €4M in potential gains
- Missed replacement: Better player now costs more

## Summary

🎯 **Goal**: Maximize team value, not protect current roster

**Strategy**:

1. Sell all peaked players immediately
1. Take profits when available (≥15%)
1. Replace with better value players
1. Only enforce squad minimums near match day

**Result**:

- Higher total team value
- Better players over time
- Never miss selling windows
- Continuous improvement

**Just run**:

```bash
rehoboam analyze
```

And follow the SELL recommendations! 🚀
