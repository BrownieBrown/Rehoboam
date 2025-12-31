# Enhanced Analysis Guide

## Overview

The analyze command provides comprehensive insights into your squad and market opportunities with predictions, comparisons, and strategic recommendations. All enhanced features are now enabled by default!

## How to Use

### Standard Analysis (With All Features - Default)

```bash
rehoboam analyze
```

This includes:

- Market opportunities
- Squad analysis with balance insights
- Trading strategies
- Value predictions
- Position analysis

### Simple Mode (Quick Analysis Only)

```bash
rehoboam analyze --simple
```

Or using the short flag:

```bash
rehoboam analyze -s
```

Use simple mode when you want just the basics without predictions.

## What's New in Enhanced Analysis?

### 1. 📊 Squad Balance & Composition

**Position Distribution**

- Shows how many players you have in each position
- Compares against recommended formation (1 GK, 3-5 DF, 3-5 MF, 2-3 FW)
- Highlights position gaps with clear warnings

**Quality Distribution**

- Categorizes players by value score:
  - Elite (80+): Your star players
  - Solid (60-79): Reliable performers
  - Average (40-59): Decent squad depth
  - Weak (\<40): Consider selling

**Financial Summary**

- Total squad value
- Average player value
- Most/least valuable players
- Current budget

**Position Needs**

- Clear recommendations on which positions need strengthening

### 2. 📈 Position Landscape Analysis

For each position (GK, DF, MF, FW), you'll see:

- **Average Value Score**: How good the available players are in that position
- **Average Efficiency**: Points per million € for that position
- **Top Performer**: Best overall player in that position
- **Best Value**: Most efficient player (best bang for buck)
- **Rising Star**: Player with best upward trend

This helps you understand:

- Which positions have the best value opportunities
- Where the market is weak/strong
- Which positions are overpriced

### 3. 🔮 Value Predictions

**For Your Squad:**
Predicts how your players' values will change over:

- 7 days (1 week)
- 14 days (2 weeks)
- 30 days (1 month)

**For Market Opportunities:**
Same predictions for top buy candidates

**Prediction Features:**

- **Form Trajectory** icons:

  - 📈 improving - Player trending upward
  - ➡️ stable - Consistent performance
  - 📉 declining - Losing value
  - 🌊 volatile - Unpredictable

- **Confidence Levels**:

  - ✓ 70%+ (green) - High confidence
  - 50-69% (yellow) - Medium confidence
  - ⚠ \<50% (red) - Low confidence

- **Data Quality**: Tells you how reliable the prediction is based on available historical data

### 4. 💡 Strategic Insights

The enhanced analysis automatically considers:

- **Squad balance** when recommending trades
- **Budget allocation** per position
- **Risk assessment** for profit opportunities
- **Market trends** for timing buys/sells

## Use Cases

### 1. Finding Players to Sell

```bash
rehoboam analyze --enhanced
```

Look at:

- Your Squad Analysis (SELL recommendations)
- Value Predictions (declining players)
- Squad Balance (positions with too many players)

### 2. Finding Players to Buy

```bash
rehoboam analyze --enhanced
```

Look at:

- Top Trading Opportunities
- Position Analysis (best value in each position)
- Market Value Predictions (improving players)
- Squad Balance (positions you need)

### 3. Building Best 11

```bash
rehoboam analyze --enhanced
```

Look at:

- Squad Balance (shows gaps)
- Recommended Trades (optimized for best lineup)
- Quality Distribution (ensure you have enough elite players)

### 4. Profit Trading

```bash
rehoboam analyze --enhanced
```

Look at:

- Profit Trading Opportunities table
- Market Value Predictions (7-14 day predictions)
- Risk levels
- Expected appreciation %

## Example Workflow

### Scenario: Rebuilding with Only 3 Players

1. **Run enhanced analysis**:

   ```bash
   rehoboam analyze --enhanced
   ```

1. **Check Squad Balance**:

   - You'll see exactly which positions you need
   - Example: "Need 1 Goalkeeper, 2 Defenders, 2 Midfielders"

1. **Review Position Analysis**:

   - Find which positions have the best value right now
   - Example: "Defenders average 236.3 pts/M€ - great value!"

1. **Check Value Predictions**:

   - See which market players are trending up
   - Buy rising players before they get expensive

1. **Review Budget Allocation**:

   - System recommends how to split your budget
   - Example: 30% on defenders, 35% on midfielders, etc.

## Tips for Using Enhanced Analysis

### For Sellers

- Focus on players with 📉 declining form trajectory
- Sell elite players before tough fixtures (shown in schedule)
- Pay attention to "peaked and declining" warnings

### For Buyers

- Look for 📈 improving form with high confidence
- Buy players far below peak value (recovery opportunity)
- Check Position Analysis for best value positions
- Use predictions to time your purchases

### For Squad Building

- Always check Squad Balance first
- Fill critical gaps (GK, minimum defenders) before luxury buys
- Use Quality Distribution to ensure you're not too weak anywhere
- Balance between high-value assets and efficient budget players

## Performance Notes

The enhanced analysis takes longer (30-60 seconds extra) because it:

- Fetches historical trend data for predictions
- Analyzes performance data for form calculations
- Runs position comparisons across all players
- Calculates optimal squad balance

This is normal! The insights are worth the wait.

## Comparison with Standard Analysis

| Feature               | Standard | Enhanced |
| --------------------- | -------- | -------- |
| Market Opportunities  | ✓        | ✓        |
| Squad Analysis        | ✓        | ✓        |
| Profit Opportunities  | ✓        | ✓        |
| Trade Recommendations | ✓        | ✓        |
| Squad Balance         | ✗        | ✓        |
| Position Analysis     | ✗        | ✓        |
| Value Predictions     | ✗        | ✓        |
| Form Trajectory       | ✗        | ✓        |
| Strategic Insights    | Basic    | Advanced |

## Advanced Usage

### Combine with Other Flags

```bash
# Enhanced analysis in verbose mode (see debug info)
rehoboam analyze --enhanced --verbose

# Enhanced analysis for specific league
rehoboam analyze --enhanced --league 1

# Show all market players with enhanced analysis
rehoboam analyze --enhanced --all
```

## Troubleshooting

### Predictions Show 0%

- This means insufficient trend data
- Normal for new players or recently transferred
- Focus on confidence level - low confidence = unreliable prediction

### Squad Balance Shows Wrong Positions

- Make sure your players are correctly categorized in the API
- Check if you recently bought/sold players (refresh with new analyze)

### Enhanced Analysis Takes Too Long

- Normal for first run (caching historical data)
- Subsequent runs should be faster due to cache
- Reduce with: `rehoboam analyze` (without --enhanced)

## What's Coming Next

Potential future enhancements:

- Player similarity comparisons ("Find players like X")
- Formation-specific recommendations
- Historical performance charts
- Custom budget scenarios ("What if I had €50M?")
- Injury risk predictions
- Head-to-head player comparisons

______________________________________________________________________

**Pro Tip**: Run `rehoboam analyze --enhanced` daily to track how predictions change and identify emerging opportunities before others!
