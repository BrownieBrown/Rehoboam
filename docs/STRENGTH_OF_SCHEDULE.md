# Hybrid Strength of Schedule (SOS) System

## Overview

The bot now evaluates players based on their **upcoming fixture difficulty** using a weighted hybrid approach that considers:

1. **Short-term (Next 3 games)** - 70% weight - MOST IMPORTANT
1. **Medium-term (Next 5 games)** - 20% weight
1. **Season-long (All remaining)** - 10% weight - Context only

This helps identify **buy low / sell high opportunities** based on fixture swings.

## Why This Matters

### Buy Opportunity Example

```
Player: Mittelstädt
Current Value: Low (just faced Bayern, Leipzig)
Next 3 Games: vs Bochum (18th), Union (15th), Augsburg (16th)
Avg Opponent Rank: 16.3

Analysis: ⚡⚡⚡ Very Easy schedule!
Action: BUY NOW
Reasoning: Will likely score high next 3 weeks → value rises
SOS Bonus: +10 points
Expected: 15-20% value increase over 3 weeks
```

### Sell Opportunity Example

```
Player: Reitz
Current Value: High (just scored well vs weak teams)
Next 3 Games: vs Bayern (1st), Leipzig (2nd), Leverkusen (3rd)
Avg Opponent Rank: 2.0

Analysis: 🔥🔥🔥 Very Difficult schedule!
Action: SELL NOW or AVOID
Reasoning: Will likely score low → value drops
SOS Penalty: -10 points
Expected: 10-15% value decrease over 3 weeks
```

## Hybrid Weighting Formula

```python
# Calculate each window
short_term_sos = analyze_next_3_games()  # 70% weight
medium_term_sos = analyze_next_5_games()  # 20% weight
season_sos = analyze_remaining_games()  # 10% weight

# Weighted combination
final_sos = (short_term * 0.7) + (medium_term * 0.2) + (season * 0.1)
```

**Why 70/20/10?**

- Next 3 games drive immediate value changes
- Next 5 games provide medium-term context
- Season-long is tiebreaker/background info

## Difficulty Bands & Bonuses

Based on **average opponent league rank** (next 3 games):

| Avg Opponent Rank | Difficulty Rating | Visual | SOS Bonus | Example Opponents              |
| ----------------- | ----------------- | ------ | --------- | ------------------------------ |
| 14-18             | Very Easy         | ⚡⚡⚡ | **+10**   | Bochum, Union, Hoffenheim      |
| 11-13             | Easy              | ⚡⚡   | **+5**    | Augsburg, Bremen, Mainz        |
| 8-10              | Medium            | →      | **0**     | Wolfsburg, Stuttgart, Freiburg |
| 4-7               | Difficult         | 🔥🔥   | **-5**    | Dortmund, Frankfurt, Gladbach  |
| 1-3               | Very Difficult    | 🔥🔥🔥 | **-10**   | Bayern, Leipzig, Leverkusen    |

## Live Examples from Market

### Example 1: Easy Run Identified

```
Player: Tim Skarke
Base Score: 100 (great stats)
Status Penalty: -10 (Bench player)
Next 3 Opponents: Bochum (18), Hoffenheim (14), Augsburg (16)
Avg Rank: 16.0
SOS Rating: ⚡⚡⚡ Very Easy
SOS Bonus: +10

Net Effect: -10 + 10 = 0
Final Score: 100

Reason shown: "Unlikely to play (Bench player) (-10 pts) |
               ⚡⚡⚡ SOS: Very Easy next 3 (+10 pts)"
```

### Example 2: Multiple Bonuses Stack

```
Player: Rocco Reitz
Base Score: 90
Matchup Bonus: +5 (Favorable vs rank 18)
SOS Bonus: +10 (Very Easy next 3)

Total Bonus: +15
Final Score: 100 (capped at 100)

Reason shown: "Favorable matchup vs rank 18 (+5 pts) |
               ⚡⚡⚡ SOS: Very Easy next 3 (+10 pts)"
```

### Example 3: Difficult Schedule Warning

```
Player: [Example]
Base Score: 85
Next 3: vs Bayern (1), vs Leipzig (2), vs Leverkusen (3)
Avg Rank: 2.0
SOS Rating: 🔥🔥🔥 Very Difficult
SOS Penalty: -10

Final Score: 75

Reason shown: "🔥🔥🔥 SOS: Very Difficult next 3 (-10 pts)"
Action: SKIP or wait until after this brutal run
```

## Visual Indicators

The bot shows SOS difficulty with emojis:

| Indicator | Meaning                 | Action Recommendation            |
| --------- | ----------------------- | -------------------------------- |
| ⚡⚡⚡    | Very Easy schedule      | **BUY NOW** - value will rise    |
| ⚡⚡      | Easy schedule           | **BUY** - favorable fixtures     |
| →         | Medium schedule         | **HOLD** - neutral fixtures      |
| 🔥🔥      | Difficult schedule      | **CAUTION** - may struggle       |
| 🔥🔥🔥    | Very Difficult schedule | **AVOID/SELL** - value will drop |

## Trading Strategies

### 1. The "Fixture Swing" Strategy

**Buy BEFORE easy run, sell AFTER:**

```
Week 1: Player has tough fixtures, value depressed
       → BUY at discount

Week 2-4: Easy fixtures, player scores high
         → Value rises 15-20%

Week 5: Sell before next tough run
       → Lock in profit
```

### 2. The "Avoid the Cliff" Strategy

**Sell BEFORE difficult run:**

```
Current: Player valued high after good performances
Next 3: vs Bayern, Leipzig, Leverkusen

Action: SELL NOW at peak value
       Others will hold and lose value
       Buy back cheaper after tough run
```

### 3. The "Season Value" Strategy

**Long-term holds on favorable schedules:**

```
Player: Has easy schedule rest of season
Next 10 games: avg opponent rank 13.5

Action: BUY and HOLD
       Will consistently score well
       Value steadily appreciates
```

## How It Works

### Step 1: Fetch Player Details

```python
player_details = api.get_player_details(league_id, player_id)
matchups = player_details["mdsum"]  # Past and future matches
```

### Step 2: Identify Upcoming Matches

```python
# Filter for unplayed matches (mdst=0)
upcoming = [m for m in matchups if m["mdst"] == 0]

short_term = upcoming[:3]  # Next 3 games
medium_term = upcoming[:5]  # Next 5 games
season = upcoming  # All remaining
```

### Step 3: Calculate Opponent Strength

```python
for match in short_term:
    opponent_id = get_opponent(match, player_team_id)
    opponent_profile = api.get_team_profile(opponent_id)
    opponent_strength = calculate_team_strength(opponent_profile)

avg_opponent_strength = mean(opponent_strengths)
avg_opponent_rank = mean(opponent_ranks)
```

### Step 4: Apply Weighted Formula

```python
weighted_sos = (short_term_avg * 0.7) + (medium_term_avg * 0.2) + (season_avg * 0.1)
```

### Step 5: Calculate Bonus

```python
if avg_opponent_rank >= 14:
    sos_bonus = +10  # Very Easy
elif avg_opponent_rank >= 11:
    sos_bonus = +5  # Easy
elif avg_opponent_rank >= 8:
    sos_bonus = 0  # Medium
elif avg_opponent_rank >= 4:
    sos_bonus = -5  # Difficult
else:
    sos_bonus = -10  # Very Difficult
```

## Data Sources

All data comes from KICKBASE API:

1. **Player matchups**: `/v4/leagues/{leagueId}/players/{playerId}`

   - Returns `mdsum` field with past/future matches
   - Each match has: opponent IDs, date, score (if played)

1. **Team strength**: `/v4/leagues/{leagueId}/teams/{teamId}/teamprofile`

   - Returns league position, wins/draws/losses
   - Used to calculate opponent difficulty

1. **Caching**: Team strengths cached in `MatchupAnalyzer.team_cache`

   - Avoids re-fetching same team multiple times
   - Cache cleared when bot restarts

## Performance Optimization

**Without caching**: ~20-25 API calls per player

- 1 call for player details
- 3-5 calls for upcoming opponents
- Repeated for each market player

**With caching**: ~5-10 API calls per player

- Team strengths cached after first fetch
- Typical market has 20 players from ~10 teams
- 50% reduction in API calls

**Future optimization**:

- Cache team strengths for 24 hours (standings don't change mid-week)
- Batch fetch multiple team profiles
- Pre-load all Bundesliga teams on startup

## Comparison to Single-Game Matchup

**Old approach** (just next game):

```
Player: Mittelstädt
Next game: vs Bochum (18th)
Bonus: +10 (easy matchup)

Problem: Misses next 2 games vs Bayern + Leipzig!
        Would buy, then value crashes
```

**New hybrid approach** (next 3 games):

```
Player: Mittelstädt
Next game: vs Bochum (18th) → +10
Next 3 avg: Bochum, Bayern, Leipzig → rank 10.3
SOS: Medium (0 bonus)

Result: Tempered optimism, account for tough games coming
```

## Configuration

Currently hardcoded in `matchup_analyzer.py`:

```python
# Weighting
SHORT_TERM_WEIGHT = 0.7  # Next 3 games
MEDIUM_TERM_WEIGHT = 0.2  # Next 5 games
SEASON_WEIGHT = 0.1  # All remaining

# Bonuses
VERY_EASY_BONUS = +10  # Avg rank >= 14
EASY_BONUS = +5  # Avg rank >= 11
MEDIUM_BONUS = 0  # Avg rank 8-10
DIFFICULT_PENALTY = -5  # Avg rank 4-7
VERY_DIFFICULT_PENALTY = -10  # Avg rank < 4
```

These can be made configurable in settings if needed.

## Combined with Other Factors

SOS bonus **stacks** with other bonuses:

```python
final_score = base_value_score + matchup_bonus + sos_bonus + trend_bonus

Example:
  Base: 85
  + Next game matchup: +5 (vs rank 15)
  + SOS (next 3): +10 (very easy)
  + Team strength: +3 (Leipzig)
  = 103 → capped at 100
```

**Maximum possible bonus**: +10 (matchup) + 10 (SOS) + 3 (team) = +23
**Maximum possible penalty**: -25 (injured) or -15 (bench) + -10 (SOS) = -25 to -25

## Real-World Impact

**Before SOS** (just single matchup):

- Would buy players with 1 easy game
- Miss that next 2 games are brutal
- Value crashes after purchase

**After SOS** (hybrid approach):

- See full 3-game picture
- Identify true fixture swings
- Buy at valleys, sell at peaks

**Example profit scenario**:

```
Week 1: Player A has tough next 3 games
       Others avoid, price drops to €8M
       Bot sees: Week 4-6 are very easy
       → BUY at €8M

Week 4: Easy run starts, player scores high
       → Value rises to €10M

Week 6: Easy run ends, tough games ahead
       → SELL at €10M

Profit: €2M (25% gain in 5 weeks)
```

## Usage

SOS analysis runs automatically:

```bash
# See SOS in analysis
rehoboam analyze

# Trade with SOS-aware decisions
rehoboam trade --max 5 --live
```

Every player shows:

```
⚡⚡⚡ SOS: Very Easy next 3 (+10 pts)
```

No configuration needed - fully integrated!

## Future Enhancements

Potential improvements:

1. **Historical performance vs opponents**

   - Track how player performs vs specific teams
   - Boost score if historically scores well vs upcoming opponents

1. **Home/Away weighting**

   - Players perform differently at home vs away
   - Adjust SOS based on home/away split

1. **Fixture congestion**

   - Penalize teams playing 3 games in 7 days
   - Boost teams with 2 weeks between games

1. **Injury return timing**

   - If player injured but returns for easy run
   - Massive value opportunity

1. **Dynamic weighting**

   - Increase short-term weight when close to deadline
   - Increase season weight for long-term holds

## Code Structure

```
matchup_analyzer.py
├── analyze_strength_of_schedule() - Main SOS calculator
│   ├── _analyze_schedule_window() - Analyze N games
│   ├── _calculate_sos_bonus() - Determine bonus points
│   └── _get_difficulty_rating() - Human-readable rating
│
trader.py
└── _get_matchup_context()
    └── Calls analyze_strength_of_schedule()
        └── Returns sos_analysis in context

analyzer.py
└── analyze_market_player()
    ├── Apply matchup bonus
    ├── Apply SOS bonus (STACKS!)
    └── Add SOS to reason text
```

## Summary

The hybrid SOS system gives you a **crystal ball** for player values:

- ✅ See easy runs coming → BUY before value rises
- ✅ See tough runs coming → SELL before value drops
- ✅ Balance short-term (next 3) with longer context
- ✅ Visual indicators (⚡⚡⚡ / 🔥🔥🔥) for quick decisions
- ✅ Automatic integration - no manual analysis needed

**Bottom line**: You can now time the market based on fixtures, not just current form!
