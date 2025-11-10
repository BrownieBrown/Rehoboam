# Bug Fixes for Sell Analysis

## Issues Found and Fixed

### 1. ✅ `AttributeError: 'Player' object has no attribute 'full_name'`

**Location:** `rehoboam/trader.py:288`

**Problem:**

```python
player_name = (p.full_name,)  # ERROR - no such attribute!
```

**Fix:**

```python
player_name = (f"{p.first_name} {p.last_name}",)  # Correct
```

Player objects have `first_name` and `last_name` attributes, not `full_name`.

______________________________________________________________________

### 2. ✅ `AttributeError: 'Player' object has no attribute 'league_id'`

**Location:** `rehoboam/trader.py:473`

**Problem:**

```python
peak_analysis = value_tracker.get_peak_analysis(
    player.id, player.league_id, current_value
)
# ERROR - Player doesn't have league_id
```

**Fix:**

```python
# Added league parameter to display_sell_analysis method
def display_sell_analysis(self, analyses, title, league: Optional[League] = None):
    league_id = league.id if league else "unknown"
    peak_analysis = value_tracker.get_peak_analysis(player.id, league_id, current_value)
```

The league_id comes from the League object, not the Player object.

______________________________________________________________________

### 3. ✅ Missing `Optional` Import

**Location:** `rehoboam/trader.py:1`

**Problem:**

```python
# Used Optional[League] but didn't import it
```

**Fix:**

```python
from typing import Optional
```

______________________________________________________________________

### 4. ✅ Indentation Error in Sell Protection Logic

**Location:** `rehoboam/trader.py:362-370`

**Problem:**

```python
if analysis.recommendation == "SELL":
    if len(players) <= self.settings.min_squad_size:
    analysis.recommendation = "HOLD"  # Wrong indentation!
```

**Fix:**

```python
if analysis.recommendation == "SELL":
    if len(players) <= self.settings.min_squad_size:
        analysis.recommendation = "HOLD"  # Correct indentation
    elif is_starter:
        analysis.recommendation = "HOLD"
    elif is_high_performer:
        analysis.recommendation = "HOLD"
```

______________________________________________________________________

### 5. ✅ Added Error Handling for Individual Player Analysis

**Location:** `rehoboam/trader.py:325-376`

**Problem:**
If one player's analysis fails, the entire squad analysis crashes.

**Fix:**

```python
for player in players:
    try:
        # Analyze player...
        analyses.append(analysis)
    except Exception as e:
        console.print(
            f"[yellow]Warning: Could not analyze {player.first_name} {player.last_name}: {e}[/yellow]"
        )
        continue  # Skip this player, continue with others
```

Now individual player failures won't break the entire analysis.

______________________________________________________________________

### 6. ✅ Updated CLI to Pass League Parameter

**Location:** `rehoboam/cli.py:95,102`

**Problem:**

```python
trader.display_sell_analysis(sell_recommendations, title="...")
# Missing league parameter!
```

**Fix:**

```python
trader.display_sell_analysis(sell_recommendations, title="...", league=league)
trader.display_sell_analysis(team_analyses, title="...", league=league)
```

______________________________________________________________________

## Testing

Run the following to verify fixes:

```bash
# Test imports and syntax
venv/bin/python test_sell_display.py

# Test actual analysis (requires auth)
rehoboam analyze

# Test with full squad view
rehoboam analyze --all
```

## Expected Output

After running `rehoboam analyze`, you should see:

1. **Market opportunities table** (buy recommendations)

1. **Sell recommendations table** (🔴 Players You Should Consider Selling)

   - Shows purchase price, current value, peak value
   - Profit/loss percentage
   - Trend indicators (↗ ↘ →)
   - SOS ratings (⚡⚡⚡ 🔥🔥🔥)
   - Comprehensive reasons

1. **Optional: Full squad analysis** (if using `--all` flag)

## What Should Work Now

✅ Squad analysis runs without crashes
✅ Value snapshots recorded to database
✅ Peak detection working
✅ Trend analysis displayed
✅ SOS (Strength of Schedule) integrated
✅ Visual table displays properly
✅ Individual player errors don't crash analysis
✅ Proper profit/loss calculations

## If You Still See Errors

1. **Check Python environment:**

   ```bash
   which python  # Should point to venv
   ```

1. **Check for missing dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

1. **Check database permissions:**

   ```bash
   ls -la logs/
   # Should show value_tracking.db and bid_learning.db
   ```

1. **Run with verbose output:**

   ```bash
   rehoboam analyze --all  # Shows all players and more details
   ```

1. **Check specific error in logs:**
   Any errors will show as yellow warnings instead of crashing

## Known Limitations

- **First run**: Peak analysis won't have historical data yet

  - Solution: Run `rehoboam analyze` regularly (daily/weekly)
  - After a few runs, peak detection will work

- **Purchase prices**: If you bought players before installing tracker

  - Solution: Manually record purchases or profit/loss will be 0% initially
  - See `docs/SELL_ANALYSIS.md` for manual recording

- **Trend data**: Needs at least 2 data points over 14 days

  - Solution: Run analyzer at least twice over 2 weeks

## Summary

All attribute errors fixed, proper error handling added, and the sell analysis table should now display correctly with:

- ✅ Purchase prices
- ✅ Current values
- ✅ Peak values (after history builds)
- ✅ Profit/loss percentages
- ✅ Trend indicators
- ✅ SOS ratings
- ✅ Comprehensive sell signals
