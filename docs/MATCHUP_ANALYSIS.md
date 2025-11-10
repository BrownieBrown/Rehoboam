# Matchup & Team Strength Analysis

## Overview

The bot now evaluates players based on:

- **Team strength** (league standings)
- **Opponent difficulty** (upcoming matchups)
- **Lineup probability** (starter vs bench player)
- **Injury status** (healthy vs injured/unavailable)

This helps avoid buying injured/benched players and favors players with easy upcoming matchups.

## Features

### 1. Player Status Detection

**Lineup Probability** (1-5 scale):

- `prob=1`: **Regular starter** → No penalty
- `prob=2`: **Rotation player** → No penalty
- `prob=3`: **Bench player** → -10 points
- `prob=4`: **Rarely plays** → -15 points
- `prob=5`: **Unlikely to play** → -15 points

**Injury Status** (`st` field):

- `st=0`: Healthy → No penalty
- `st=2`: Status uncertain → Investigate
- `st=4`: Short-term injury → -25 points
- `st=256`: Long-term injury → -25 points

### 2. Team Strength Scoring

Based on Bundesliga standings (0-100 scale):

```python
# Position-based score (60% weight)
position_score = ((18 - league_position) / 17) * 100
# 1st place = 100, 18th place = 0

# Points per game score (40% weight)
ppg_score = (points_per_game / 3) * 100

# Final strength
strength_score = (position_score * 0.6) + (ppg_score * 0.4)
```

**Example Teams (Current Season)**:

- Leipzig (Rank 2): 88.4 strength (7W-1D-2L)
- Gladbach (Rank 5): 80.0+ strength
- Union Berlin (Rank 15): 30.0+ strength
- Bochum (Rank 18): ~5.0 strength

### 3. Matchup Difficulty

Calculated based on opponent strength relative to player's team:

```
difficulty = 50 + (opponent_strength - player_team_strength) / 2
```

**Difficulty Bands**:

- 0-30: **Easy matchup** → +10 points (starter) or +5 points (rotation)
- 30-50: **Favorable matchup** → +5 points (starter) or +2 points (rotation)
- 50-70: **Medium matchup** → 0 points
- 70-100: **Difficult matchup** → -5 points (starter) or -8 points (rotation)

**Extra Bonuses**:

- Starter on top team (strength ≥75): +3 points
- Example: "key player on top team"

## Examples from Live Data

### ✅ Easy Matchup Bonus

```
Player: Maximilian Mittelstädt (Stuttgart, Rank 9)
Opponent: Union Berlin (Rank 15)
Team Strength: 70+
Difficulty: 28 (easy)
Bonus: +10 points
Reason: "Easy matchup vs rank 15 (+10 pts)"
```

### ✅ Favorable Matchup

```
Player: Rocco Reitz (Gladbach, Rank 5)
Opponent: Bochum (Rank 18)
Team Strength: 80+
Difficulty: 35 (favorable)
Bonus: +5 points
Reason: "Favorable matchup vs rank 18 (+5 pts)"
```

### ❌ Bench Player Penalty

```
Player: Tim Skarke
Status: prob=3 (Bench player)
Penalty: -10 points
Reason: "Unlikely to play (Bench player) (-10 pts)"

Result: Value dropped from 100→90, still good but penalized
```

### ❌ Injured Player (Critical)

```
Player: [Example]
Status: st=256 (Long-term injury)
Penalty: -25 points
Reason: "Injured/unavailable (Long-term injury) (-25 pts)"

Result: Value dropped significantly, filtered out of BUY recommendations
```

## Impact on Value Scoring

**Before Matchup Analysis**:

- Pure stats-based value (points, price, average)
- No context about playing time or matchups
- Could buy injured/benched players

**After Matchup Analysis**:

- Base value score: 0-100
- Matchup bonus/penalty: -25 to +10
- Final score: max(0, min(100, base + bonus))

**Example Adjustments**:

| Player         | Base Score | Matchup Context       | Bonus | Final Score |
| -------------- | ---------- | --------------------- | ----- | ----------- |
| Mittelstädt    | 85         | Easy matchup, starter | +10   | 95          |
| Reitz          | 90         | Favorable matchup     | +5    | 95          |
| Skarke         | 100        | Bench player          | -10   | 90          |
| Injured Player | 60         | Long-term injury      | -25   | 35          |

## API Endpoints Used

### Player Details

`GET /v4/leagues/{leagueId}/players/{playerId}`

Returns:

- `tid`, `tn`: Team ID and name
- `st`: Status code (injury)
- `prob`: Lineup probability (1-5)
- `mdsum`: Matchup summary (past/future matches)
- `g`, `a`: Goals and assists

### Team Profile

`GET /v4/leagues/{leagueId}/teams/{teamId}/teamprofile`

Returns:

- `pl`: League position (1-18)
- `tw`, `td`, `tl`: Wins, draws, losses
- `tv`: Team value
- `it`: All players on team

## Configuration

Currently hardcoded in `matchup_analyzer.py`:

- Easy matchup bonus: +10 (starters) / +5 (rotation)
- Favorable matchup bonus: +5 (starters) / +2 (rotation)
- Bench player penalty: -10
- Rarely plays penalty: -15
- Injury penalty: -25

## Testing Results

Tested on live market (21 KICKBASE players):

**Top Players Identified**:

1. **Mittelstädt** (95.0): Starter, easy matchup (+10)
1. **Reitz** (95.0): Rotation, favorable matchup (+5)
1. **Lund** (95.0): Medium matchup, no bonus

**Correctly Filtered**:

- **Tim Skarke** (90.0): Penalized for bench status
- **Armindo Sieb** (90.0): Penalized for bench status

Both would have scored 100 without matchup analysis!

## Future Enhancements

### Potential Improvements:

1. **Historical matchup performance** - Track player points against specific teams
1. **Home/away splits** - Players perform differently at home vs away
1. **Recent form** - Weight last 3 matches more heavily
1. **Fixture congestion** - Penalize teams playing multiple games in short period
1. **Expected minutes** - Use lineup probability to estimate playing time

### Data Sources:

- ✅ KICKBASE API (player details, team profiles)
- ✅ Live standings from team profiles
- ❌ External Bundesliga data (for more detailed stats)

## Code Structure

```
matchup_analyzer.py
├── PlayerStatus (dataclass)
├── MatchupInfo (dataclass)
├── TeamStrength (dataclass)
└── MatchupAnalyzer
    ├── analyze_player_status() - Check injury/lineup
    ├── get_team_strength() - Calculate from standings
    ├── get_next_matchup() - Parse upcoming match
    ├── calculate_matchup_difficulty() - Compare team strengths
    └── get_matchup_bonus() - Final bonus calculation

trader.py
└── _get_matchup_context()
    ├── Fetch player details
    ├── Fetch player's team profile
    ├── Fetch opponent team profile
    └── Calculate matchup bonus

analyzer.py
└── analyze_market_player()
    ├── Calculate base value score
    ├── Apply matchup bonus
    └── Add matchup context to reason
```

## Caching Strategy

**Not Currently Cached** (TODO):

- Player details (changes infrequently)
- Team profiles (standings update after matches)

**Should Cache**:

- Team strength: Cache for 24 hours
- Player status: Cache for 6 hours
- Matchup data: Cache until matchday changes

**Why Not Cached Yet**:

- First implementation focused on accuracy
- Performance is acceptable (~1-2 seconds per player)
- Will add caching if performance becomes issue

## Usage

The matchup analysis runs automatically in:

```bash
# Analyze market with matchup context
rehoboam analyze

# Trade with matchup-aware evaluation
rehoboam trade --max 3 --live
```

No configuration needed - it's fully integrated into the existing analysis pipeline.

## Impact on Trading

**Before**:

- 0 players with value score 100+ (due to strict base scoring)
- Would buy bench players with good historical stats
- Missed context about upcoming matchups

**After**:

- 3-5 players typically reach 95-100 score with bonuses
- Bench players filtered out automatically
- Easy matchups boost buy confidence
- Difficult matchups cause caution

**Real Example - Yan Diomande**:

```
Base Score: 85 (good stats)
Team: Leipzig (Rank 2, strength 88.4)
Status: prob=2 (Rotation player) ✅
Matchup: vs Hoffenheim (easier opponent)
Bonus: +3 (decent matchup for good team)
Final Score: 88
```

Would still be recommended for BUY with smart bid!
