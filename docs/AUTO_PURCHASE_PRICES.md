# Automatic Purchase Price Detection

## Overview

The bot now **automatically fetches purchase prices** from the KICKBASE API! No manual recording needed.

## What Changed

### Before (Manual)

```bash
# Had to manually record every player
rehoboam record-purchase "Danel Sinani" 6000000
rehoboam record-purchase "Player 2" <price>
...
# 25+ commands for full squad 😫
```

### After (Automatic)

```bash
# Just run analyze - bot fetches everything!
rehoboam analyze

# Output:
Fetching purchase prices from API...
✓ Found 25 purchase price(s) from API

# All purchase prices, history, and peaks loaded automatically! ✅
```

## API Endpoint

### `/v4/leagues/{leagueId}/players/{playerId}/marketvalue/{timeframe}`

**Returns:**

```json
{
  "trp": 6000000,  // Transfer price - What you paid! 🎯
  "it": [          // Historical data array
    {"dt": 19723, "mv": 6000000},   // dt = days since 1970-01-01, mv = market value
    {"dt": 19730, "mv": 7500000},
    {"dt": 19737, "mv": 12000000},
    {"dt": 19744, "mv": 17000000},
    {"dt": 19751, "mv": 14000000}
  ],
  "hmv": 17000000, // Highest market value (peak!) 🏔️
  "lmv": 6000000,  // Lowest market value
  "iso": true,     // Unknown flag
  "idp": true      // Unknown flag
}
```

## How It Works

### 1. Fetch Purchase Price

```python
# For each player in your squad
market_value_data = api.get_player_market_value_history(
    league_id, player_id, timeframe=30
)
transfer_price = market_value_data.get("trp")  # Transfer price field

# Sinani example:
transfer_price = 6000000  # €6M
current_value = 14000000  # €14M
profit = 8000000  # +133%
```

### 2. Import Historical Data

```python
# Bot imports historical data from "it" array
historical_items = market_value_data.get("it", [])
for item in historical_items:
    days_since_epoch = item["dt"]  # Days since 1970-01-01
    market_value = item["mv"]       # Market value on that day
    timestamp = days_since_epoch * 86400  # Convert to Unix timestamp

# Use API's pre-calculated peak
peak_value = market_value_data.get("hmv", 0)  # 17000000
decline = -17.6%  # From peak to current
```

### 3. Calculate Profit/Loss

```python
profit_pct = ((current_value - transfer_price) / transfer_price) * 100

# Sinani:
# (14M - 6M) / 6M * 100 = +133.3%
```

### 4. Detect Peaks Immediately

```python
# No need to wait 7 days!
# Historical data imported from API
# Peak detection works on first run

peak = max(market_values)  # €17M
if current < peak - 5%:
    recommendation = "SELL"  # Peaked and declining!
```

## Example Output

### Console Output

```bash
$ rehoboam analyze

Fetching your squad...
✓ You have 25 players in squad

Recorded value snapshots for 25 players
Value tracking: 150 snapshots over 30 days  # Imported from API!

Fetching purchase prices from API...
✓ Found 25 purchase price(s) from API

📊 Your Squad Analysis
────────────────────────────────────────────────────────────────────────
Player          Purchase    Current     Peak         Profit/Loss  Recommendation
────────────────────────────────────────────────────────────────────────
Danel Sinani    €6,000,000  €14,000,000 €17,000,000  +133.3%     SELL
                                        -17.6%

Florian Wirtz   €15,000,000 €20,000,000 €20,000,000  +33.3%      HOLD
                                        at peak

... (all players with purchase prices!)
────────────────────────────────────────────────────────────────────────

📋 Recommendations: 3 SELL, 22 HOLD

⚠️  Found 3 player(s) you should consider selling!
```

### Danel Sinani Example

```
📊 Danel Sinani Analysis

Purchase Price: €6,000,000 (from API ✓)
Current Value:  €14,000,000
Peak Value:     €17,000,000
Days Since Peak: 11 days

Profit: €8,000,000 (+133.3%)
Decline from Peak: -€3,000,000 (-17.6%)

Recommendation: SELL 🔴
Reason: Peaked and declining -17.6% over 11d

30-Day Trend: ↘ Falling (-12.5%)
Next 3 Games: Medium schedule
```

## Benefits

### ✅ Automatic

- No manual recording needed
- Works on first run
- Always up-to-date

### ✅ Historical Data

- Imports 30 days of history
- Peak detection immediate
- Trend analysis from day 1

### ✅ Accurate Profit/Loss

- Shows exact purchase price
- Calculates real profit %
- Detects missed peaks

### ✅ Fallback System

```python
1. Try API (transferPrice)
   ↓ (if not found)
2. Try local DB (manual recordings)
   ↓ (if not found)
3. Use current value (0% profit)
```

## When Purchase Price Unavailable

Some scenarios where API might not have purchase price:

### 1. Initial Squad Players

```
Players you had when joining league
No transfer price recorded

Fallback: Uses current value (0% profit)
```

### 2. Very Old Purchases

```
Transfers from >30 days ago (outside timeframe)

Solution: Increase timeframe parameter
api.get_player_market_value_history(league_id, player_id, timeframe=90)
```

### 3. API Error

```
Network issue or rate limit

Fallback: Local DB or current value
```

## Testing

### Test API Endpoint

```bash
# Test fetching purchase prices for your squad
python test_purchase_api.py

# Output shows:
# - Purchase prices for each player
# - Profit/loss calculations
# - Peak detection
# - Historical trends
```

### Example Test Output

```bash
$ python test_purchase_api.py

TESTING PURCHASE PRICE API
═════════════════════════════════════════════════════════

1. Logging in...
✓ Logged in successfully

2. Fetching leagues...
✓ Using league: My League

3. Fetching your squad...
✓ Found 25 players

4. Testing market value history endpoint...

📊 Danel Sinani (FW)
   Current value: €14,000,000
   Purchase price: €6,000,000
   Profit/Loss: €8,000,000 (+133.3%)
   💰 Excellent profit!
   📈 Historical data: 30 data points
   30-day trend: €6,000,000 → €14,000,000 (+133.3%)
   🏔️  Peak: €17,000,000 (now -17.6% below)

📊 Florian Wirtz (MF)
   Current value: €20,000,000
   Purchase price: €15,000,000
   Profit/Loss: €5,000,000 (+33.3%)
   💰 Excellent profit!
   📈 Historical data: 30 data points
   30-day trend: €15,000,000 → €20,000,000 (+33.3%)

... (all players) ...

SUMMARY
═════════════════════════════════════════════════════════

✅ API Endpoint Working!
   • Fetches purchase prices (trp field)
   • Fetches historical market values
   • Can detect peaks and trends

✅ No Manual Recording Needed!
   • Bot will automatically fetch purchase prices
   • Bot will import 30 days of history
   • Peak detection works immediately

💡 Just run: rehoboam analyze
```

## Code Changes

### Added Endpoint

```python
# In kickbase_client.py
def get_player_market_value_history(
    self, league_id: str, player_id: str, timeframe: int = 30
) -> Dict[str, Any]:
    """
    GET /v4/leagues/{league_id}/players/{player_id}/marketvalue/{timeframe}

    Returns:
      - trp: Transfer price - What you paid
      - it: Array of historical data [{"dt": days_since_epoch, "mv": market_value}]
      - hmv: Highest market value in timeframe (peak)
      - lmv: Lowest market value in timeframe
    """
```

### Updated Squad Analysis

```python
# In trader.py analyze_team()

# For each player:
1. Fetch market value history from API
2. Extract transferPrice (what you paid)
3. Import 30 days of historical data
4. Detect peaks automatically
5. Calculate profit/loss
6. Generate SELL recommendations
```

## Migration from Manual Recording

### If You Already Recorded Purchases

```bash
# No problem! Bot will:
# 1. Fetch from API (newer data)
# 2. Override local DB with API data
# 3. Keep local DB as fallback

# Your manual recordings won't be lost,
# just overridden with more accurate API data
```

### If You Haven't Recorded Anything

```bash
# Perfect! Just run:
rehoboam analyze

# Bot automatically:
# ✓ Fetches all purchase prices
# ✓ Imports historical data
# ✓ Detects peaks
# ✓ Shows sell recommendations
```

## Troubleshooting

### "No purchase prices found"

**Possible causes:**

1. Initial squad players (no transfer recorded)
1. API field not available (`trp` field missing)
1. Network error

**Solution:**

```bash
# Test API directly
python test_purchase_api.py

# Check what fields are returned
# Update field names if needed
```

### "Peak detection not working"

**Check:**

```bash
# Are historical values being imported?
ls -la logs/value_tracking.db

# Should be >10KB if history imported
```

**If empty:**

- API might not return `marketValues` field
- Check `test_purchase_api.py` output
- May need to adjust field names

### "Profit/loss is 0%"

**Likely:**

- Player had no `transferPrice` in API
- Using fallback (current value = purchase price)

**Acceptable for:**

- Initial squad players
- Very old transfers

## Summary

🎯 **Key Improvement**: No manual work needed!

**Before:**

- Manual recording for all 25 players
- No historical data
- Wait 7 days for peak detection

**After:**

- Automatic from API
- 30 days of history imported
- Peak detection on first run

**Just run:**

```bash
rehoboam analyze
```

And see all your purchase prices, peaks, and sell recommendations automatically! 🚀
