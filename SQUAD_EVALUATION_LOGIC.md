# Squad Evaluation Logic

## How the Bot Decides Who to Keep vs Sell

When you have 15 players but need only 11 for gameday, the bot selects your **Best 11** and evaluates the remaining 4 bench players.

### Value Score Calculation (0-100 points)

Every player gets a score based on these factors:

#### 1. **Points Efficiency** (0-40 points)

- How many points per million euros?
- Example: 100 points / €10M = 10 pts/M€ → scores 40/40
- **Why it matters**: Shows bang-for-buck value

#### 2. **Historical Performance** (0-25 points)

- Season average points
- Example: 60 avg points → scores ~24/25
- **Why it matters**: Consistent performers score higher

#### 3. **Affordability Bonus** (0-15 points)

- Cheaper players get bonus points (more budget flexibility)
- \< €5M = 15 pts, €5-10M = 10 pts, €10-20M = 5 pts, > €20M = 0 pts
- **Why it matters**: Budget management

#### 4. **Current Form** (0-20 points, can be negative!)

- Recent points vs season average
- If recent > 3x average → +20 pts (hot streak!)
- If recent = 0 and avg > 50 → -15 pts (strong player benched)
- **Why it matters**: Form is temporary, but matters NOW

#### 5. **Market Momentum** (0-15 points, can be negative!)

- **Rising trend** (last 14 days): +5 to +15 pts
- **Falling trend**: -5 to -15 pts
- **Far below peak** (>40% down) + not falling → +10 pts (recovery potential!)
- **At peak but falling** → -5 pts (danger zone)
- **Why it matters**: Buy low, sell high

#### 6. **Sample Size Penalty** (0 to -30 points)

- Games played this season
- 0-1 games → -30 to -50 pts (very unreliable!)
- 2-4 games → -15 to -20 pts
- 5-6 games → -10 pts
- 7-8 games → -5 pts
- 9+ games → no penalty
- **Why it matters**: Emre Can syndrome (1 game, 100 pts = unreliable)

#### 7. **Strength of Schedule (SOS) Bonus** (+/-10 points)

- Next 3 games difficulty
- Very Easy schedule → +10 pts
- Very Difficult schedule → -10 pts
- **Why it matters**: Easy games = more points coming

#### 8. **Next Matchup Bonus** (+/-5 points)

- Immediate next opponent
- **Why it matters**: Short-term opportunity

______________________________________________________________________

## Example: Lienhart vs Hranác

Let's say both are defenders with similar stats, but here's why one might be sold:

### Scenario 1: Form Difference

**Lienhart:**

- Season avg: 40 pts
- Recent points: 15 pts (below avg) → Form score: +5
- **Total: 55/100**

**Hranác:**

- Season avg: 42 pts
- Recent points: 70 pts (hot streak!) → Form score: +20
- **Total: 70/100**

**Result:** Lienhart on bench, Hranác in Best 11

______________________________________________________________________

### Scenario 2: Market Momentum

**Lienhart:**

- Market value falling -12% in 14 days → -10 pts
- Currently 5% below peak (stable) → 0 pts
- **Momentum: -10 pts**

**Hranác:**

- Market value rising +8% in 14 days → +10 pts
- Currently 30% below peak → +7 pts (recovery potential)
- **Momentum: +17 pts**

**Result:** Lienhart loses 27 points vs Hranác!

______________________________________________________________________

### Scenario 3: Strength of Schedule

**Lienhart's next 3 games:**

- vs Bayern Munich (rank 1)
- vs Leverkusen (rank 2)
- vs Dortmund (rank 3)
- **SOS Rating: Very Difficult** → -10 pts

**Hranác's next 3 games:**

- vs Holstein Kiel (rank 17)
- vs Bochum (rank 18)
- vs Heidenheim (rank 15)
- **SOS Rating: Very Easy** → +10 pts

**Result:** Hranác gets +20 pts advantage due to schedule!

______________________________________________________________________

### Scenario 4: Sample Size (Consistency)

**Lienhart:**

- 15 games played, very consistent → no penalty

**Hranác:**

- 15 games played, very consistent → no penalty

**Result:** No difference here

______________________________________________________________________

## Which Players Get Sold?

Once Best 11 is selected, the bot looks at bench players (#12-15):

### If Budget is NEGATIVE:

- Sells weakest bench players (lowest value scores)
- Sells until budget is positive + €500K buffer
- **Example:** Budget -€3M → Sell #15, #14, #13 until positive

### If Budget is POSITIVE but Gameday Close (≤2 days):

- Sells bench players with value score \< 30 (very weak)
- Creates cash cushion for emergencies

### If Budget is HEALTHY:

- Keeps all bench for squad depth
- Only recommends selling if extremely weak (score \< 20)

______________________________________________________________________

## How to Check Your Players

Run this to see each player's breakdown:

```bash
rehoboam analyze --verbose
```

This will show:

- Value scores for all players
- SOS ratings (⚡⚡⚡ = Very Easy, 🔥🔥🔥 = Very Difficult)
- Market trends (↗ rising, ↘ falling)
- Form indicators

Look for the Squad Optimization section to see:

- Best 11 (sorted by position)
- Bench players with KEEP/SELL recommendations
- Exact reasons for each decision

______________________________________________________________________

## Why This Logic is Sound

1. **Multi-factor analysis**: Doesn't rely on just one metric
1. **Recency bias**: Values current form and upcoming schedule
1. **Value investing**: Buys low (below peak), sells high (at peak)
1. **Risk management**: Penalizes small sample sizes heavily
1. **Budget discipline**: Ensures positive by gameday
1. **Position-aware**: Respects formation requirements (1 GK, 3+ DEF, etc.)

The bot essentially asks:

- "Will this player score more points than alternatives?"
- "Is their value rising or falling?"
- "Do they have easy or hard games coming up?"
- "Can I trust their stats or is it a small sample?"
- "Do I need the money for gameday?"
