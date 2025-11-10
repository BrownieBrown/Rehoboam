# Auction Monitoring & Safe Replacement System

## Overview

The bot now uses the KICKBASE market API to accurately track auction status and execute safe player replacements.

## Key Features

### 1. **Accurate Auction Detection**

Uses `/v4/leagues/{leagueId}/market` endpoint to detect:

- ✅ Active bids (player on market with your offer)
- ✅ Won auctions (player in your squad)
- ✅ Lost auctions (player not on market, not in squad)
- ✅ Being outbid (player on market without your offer)

### 2. **Persistent State**

- Bids saved to `logs/bid_monitor_state.json`
- Survives bot restarts
- Automatic state recovery on startup

### 3. **Safe Replacement Logic**

- **NEVER** sells current player before confirming replacement acquisition
- Only executes sales AFTER winning auction
- Calculates net cost/profit before bidding
- Respects budget constraints

## Current Status: Yan Diomande Bid

```
Player: Yan Diomande
Your Bid: €14,097,338 (+€1,620,000, +13.0% overbid)
Asking Price: €12,477,338
Market Value: €12,477,338
Status: AUCTION ACTIVE (your bid registered)
Listed: 2025-11-07T23:08:41Z
```

**Planned Replacement:**

- **IF BID WINS:** Will sell Stiller to KICKBASE for €32,991,461
- **Net Profit:** €18,894,123
- **Final Budget:** €22,336,723 (positive ✅)

## Commands

### Monitor Pending Bids

```bash
# Check once (dry run)
rehoboam monitor

# Continuously watch (recommended)
rehoboam monitor --watch

# Watch and execute REAL sales when bids win
rehoboam monitor --watch --live
```

### Register Existing Bid

```bash
# Register a bid placed before monitoring system existed
rehoboam register-bid "Yan Diomande"
```

### Check All Commands

```bash
rehoboam --help
```

## How Monitoring Works

### Detection Flow

```
1. Check market endpoint for player
   ├─ Player found on market?
   │  ├─ Yes → Check if you have active offer
   │  │  ├─ Yes → STATUS: PENDING (auction active)
   │  │  └─ No → STATUS: PENDING (may be outbid)
   │  └─ No → Player not on market anymore
   │     └─ Check squad endpoint
   │        ├─ Player in squad? → STATUS: WON
   │        └─ Not in squad → STATUS: LOST
   └─ Timeout (60 min) → STATUS: TIMEOUT
```

### Safe Replacement Flow

```
1. Bid placed on target player
   └─ Register with bid monitor
      ├─ Identify replacement candidates
      ├─ Calculate net cost
      └─ Create replacement plan

2. Monitor auction status
   └─ Poll every 30 seconds
      ├─ Check market endpoint
      └─ Detect auction end

3. Auction ends
   ├─ WON → Execute replacement plan
   │  ├─ Sell old player to KICKBASE
   │  └─ Update budget
   └─ LOST → Keep current player (no changes)
```

## Market Data Fields

The enhanced `MarketPlayer` now captures:

| Field              | Description                       | Example                  |
| ------------------ | --------------------------------- | ------------------------ |
| `offer_count`      | Number of active offers           | `1`                      |
| `user_offer_price` | Your bid amount                   | `14097338`               |
| `user_offer_id`    | Your user ID if highest bidder    | `"3616202"`              |
| `listed_at`        | When player listed (ISO datetime) | `"2025-11-07T23:08:41Z"` |
| `offers`           | Array of all offer details        | `[{...}]`                |

## Example Output

### Auction Active

```
Auction still active for Yan Diomande (you have bid: €14,097,338)
```

### Auction Won

```
✓ Bid WON for Yan Diomande!

Executing Replacement Plan for Yan Diomande
✓ Sold Stiller to KICKBASE for €32,991,461

Replacement Complete!
Net profit: €18,894,123
Expected budget: €22,336,723
```

### Auction Lost

```
✗ Bid LOST for Yan Diomande
```

### Being Outbid

```
You may have been outbid on Yan Diomande (2 offer(s) active)
```

## Safety Features

### Budget Protection

- ✅ Checks available budget before bidding
- ✅ Accounts for 60% debt allowance
- ✅ Warns if budget would go negative
- ✅ Calculates expected budget after replacement

### Error Handling

- ✅ Graceful API failure recovery
- ✅ State persistence prevents data loss
- ✅ Timeout protection (60 min default)
- ✅ Dry run mode by default

### Replacement Safety

- ✅ **CRITICAL:** Never sells before confirming acquisition
- ✅ Only executes when bid status = "won"
- ✅ Checks squad to verify player arrival
- ✅ Rolls back state if sale fails

## Testing

Run the test script to verify auction detection:

```bash
python test_yan_bid.py
```

Expected output:

```
✓ Found Yan Diomande on market!
  YOUR BID: €14,097,338
  ✓ Confirmed: This is YOUR bid!
```

## Next Steps

### For Current Yan Bid

**Option 1: Watch Without Auto-Execute (Safe)**

```bash
rehoboam monitor --watch
```

This will show you when auction ends but won't automatically sell Stiller.

**Option 2: Full Auto-Execution (Recommended)**

```bash
rehoboam monitor --watch --live
```

This will automatically sell Stiller when you win Yan's auction.

### For Future Trading

The bot now automatically:

1. Analyzes replacement opportunities when buying
1. Creates replacement plans
1. Registers bids with monitor
1. Executes safe swaps when auctions end

Just use:

```bash
rehoboam trade --max 3 --live
```

## Files Modified

- `rehoboam/kickbase_client.py` - Enhanced MarketPlayer with auction data
- `rehoboam/bid_monitor.py` - Auction status detection via market endpoint
- `rehoboam/trader.py` - Safe replacement logic
- `rehoboam/cli.py` - New `monitor` and `register-bid` commands
- `logs/bid_monitor_state.json` - Persistent state storage

## Technical Details

### Auction End Detection

The system determines auction end by checking if player disappears from market:

- Player on market → Auction active
- Player not on market → Auction ended
  - Check squad to determine win/loss

This is more accurate than:

- ❌ Waiting for timeout
- ❌ Polling squad only
- ❌ Guessing based on time

### State Persistence

Bid state saved after every change:

- ✅ Bid registered
- ✅ Status updated (won/lost/timeout)
- ✅ Replacement plan executed

Format:

```json
{
  "pending_bids": {
    "10771": {
      "player_id": "10771",
      "player_name": "Yan Diomande",
      "bid_amount": 14097338,
      "placed_at": 1731107321.5,
      "status": "pending",
      "confirmed_at": null
    }
  },
  "replacement_plans": {}
}
```

## Troubleshooting

### Bid Not Detected

```bash
# Check if you have active bids
rehoboam monitor

# If bid exists but not registered:
rehoboam register-bid "Player Name"
```

### State File Corrupted

```bash
# Delete state file to reset
rm logs/bid_monitor_state.json
# Re-register bids manually
rehoboam register-bid "Player Name"
```

### Auction Not Ending

- KICKBASE auctions can last several hours
- Default timeout: 60 minutes
- Use `--watch` to monitor continuously

## Yan Diomande Timeline

**Listed:** 2025-11-07T23:08:41Z
**Your Bid Placed:** Shortly after listing
**Bid Amount:** €14,097,338 (+13.0% overbid)
**Current Status:** ⏳ Auction active, you're highest bidder

**When Auction Ends:**

- ✅ **If you win:** Bot auto-sells Stiller, you gain €18.9M
- ❌ **If you lose:** No action, Stiller stays in your squad
