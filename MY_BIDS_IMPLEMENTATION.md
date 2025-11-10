# My Bids Implementation Summary

## Problem

The bot couldn't see active bids, which caused two issues:

1. **Double-bidding**: Bot might bid on players it already bid on
1. **Inaccurate budget**: Budget calculations didn't account for pending bid amounts

## Investigation Results

### API Endpoint Discovery

Tested multiple endpoint variations to find "My Bids":

```
✗ /v4/leagues/{league_id}/mybids
✗ /v4/leagues/{league_id}/bids
✗ /v4/leagues/{league_id}/market/mybids
✗ /v4/user/bids
... (15+ variations tested)
```

### Key Finding

⚠️ **No dedicated "My Bids" endpoint exists**

The KICKBASE API design:

- **All market endpoints** include bid information via:
  - `uop` (user offer price) - **YOUR bid amount** (what you bid)
  - `uoid` (user offer ID) - **YOUR user ID** (just confirms you have a bid)
- Query parameters like `?myBids=true` **don't filter** results
- You must **filter client-side** for players where `uop > 0`

### Critical Understanding

**What We Know:**

- ✅ Our own bid amount (`uop`)
- ✅ That we have a bid (`uoid == our_id`)
- ✅ Asking price (`prc`)
- ✅ Market value (`mv`)

**What We DON'T Know:**

- ❌ Who the current highest bidder is
- ❌ What the highest bid amount is
- ❌ If we're winning or losing
- ❌ How many other bids exist
- ✅ **Only find out when auction ends** who won

This means `uoid` just confirms "you have a bid", NOT "you're the highest bidder"!

### Current Implementation (Already Optimal)

The existing code was already using the correct approach:

```python
# Get all market players
market_players = api.get_market(league)

# Filter for players with your bids
my_bids = [p for p in market_players if p.has_user_offer(user_id)]
```

## Solution Implemented

### 1. Added Convenience Methods

**File**: `rehoboam/kickbase_client.py`

```python
def get_my_bids(self, league_id: str) -> list[MarketPlayer]:
    """
    Get only players where you have active bids

    Note: The API doesn't have a dedicated "my bids only" endpoint.
    This fetches all market players and filters for players where you have an offer.
    """
    if not self.user:
        raise Exception("Not logged in. Call login() first.")

    all_market = self.get_market(league_id)
    return [p for p in all_market if p.has_user_offer(self.user.id)]
```

**File**: `rehoboam/api.py`

```python
def get_my_bids(self, league: League) -> List[MarketPlayer]:
    """Get only players where you have active bids"""
    try:
        return self.client.get_my_bids(league.id)
    except Exception as e:
        raise Exception(f"Failed to fetch my bids: {e}")
```

### 2. Updated Auto Trader - Profit Trading

**File**: `rehoboam/auto_trader.py` - `run_profit_trading_session()`

**Changes**:

1. Check active bids at start of session
1. Display all active bids with amounts
1. Calculate effective budget (budget - pending bids)
1. Allow re-bidding if smart bid > current bid + 5%
1. Show opportunities even for players we bid on
1. Track total bid exposure for budget

```python
# Check active bids - we can have multiple bids simultaneously
my_bids = self.api.get_my_bids(league)
my_bid_amounts = {p.id: p.user_offer_price for p in my_bids}

if my_bids:
    console.print(f"[cyan]📊 Active bids: {len(my_bids)}[/cyan]")
    for bid_player in my_bids:
        console.print(
            f"  - {bid_player.first_name} {bid_player.last_name}: Your bid €{bid_player.user_offer_price:,}"
        )
    console.print(f"[dim]Note: You'll find out who won when auctions end[/dim]")

# Calculate effective budget (subtract pending bids)
pending_bid_total = sum(p.user_offer_price for p in my_bids)
effective_budget = current_budget - pending_bid_total

# Check if we already have a bid on this player
current_bid = my_bid_amounts.get(opp.player.id, 0)

if current_bid > 0:
    # We already bid - should we increase?
    bid_increase_threshold = current_bid * 1.05  # Need 5% higher to re-bid

    if opp.buy_price <= current_bid:
        console.print(f"[dim]Skipping - already bid €{current_bid:,}[/dim]")
        continue
    elif opp.buy_price < bid_increase_threshold:
        console.print(f"[dim]Skipping - new bid not enough higher[/dim]")
        continue
    else:
        console.print(
            f"[yellow]⚠ Increasing bid €{current_bid:,} → €{opp.buy_price:,}[/yellow]"
        )
```

### 3. Updated Auto Trader - Lineup Trading

**File**: `rehoboam/auto_trader.py` - `run_lineup_improvement_session()`

**Changes**:

1. Check active bids at start of session
1. Calculate effective budget (budget + debt - pending bids)
1. Show warning if trade includes players we bid on
1. Allow trades with players we bid on (can have multiple bids)

```python
# Check active bids - we can have multiple bids simultaneously
my_bids = self.api.get_my_bids(league)
my_bid_amounts = {p.id: p.user_offer_price for p in my_bids}

# Calculate effective budget (subtract pending bids)
pending_bid_total = sum(p.user_offer_price for p in my_bids)
available_budget = current_budget + max_debt - pending_bid_total

# Show if trade includes players we already have bids on
players_with_bids = [p for p in trade.players_in if p.id in my_bid_amounts]
if players_with_bids:
    console.print(
        f"[yellow]⚠ Trade includes players you bid on: {', '.join(p.first_name + ' ' + p.last_name for p in players_with_bids)}[/yellow]"
    )
    console.print(
        f"[dim]Bot will attempt trade anyway - you can have multiple bids[/dim]"
    )
```

## Example Output

### With Active Bids - No Re-bidding Needed:

```
🤖 Auto-Trading: Profit Opportunities
📊 Active bids: 2
  - Dominik Kohr: Your bid €9,281,213
  - Robin Hack: Your bid €7,226,114
Note: You'll find out who won when auctions end

Found 5 opportunities
Current budget: €18,199,803
Pending bids: €16,507,327
Effective budget: €1,692,476

Skipping Dominik Kohr - already bid €9,281,213
Skipping Robin Hack - already bid €7,226,114
Buying Lucas Höler for €1,200,000
✓ Buy order placed for Lucas Höler
```

### With Active Bids - Re-bidding:

```
🤖 Auto-Trading: Profit Opportunities
📊 Active bids: 2
  - Dominik Kohr: Your bid €9,281,213
  - Robin Hack: Your bid €7,226,114

Found 5 opportunities
Current budget: €18,199,803
Pending bids: €16,507,327
Effective budget: €1,692,476

⚠ Dominik Kohr - increasing bid €9,281,213 → €10,500,000
✓ Buy order placed for Dominik Kohr
Buying Lucas Höler for €1,200,000
✓ Buy order placed for Lucas Höler
```

### Without Active Bids:

```
🤖 Auto-Trading: Profit Opportunities
Found 5 opportunities
Current budget: €18,199,803

Buying Lucas Höler for €1,200,000
✓ Buy order placed for Lucas Höler
Buying Robin Hack for €7,226,114
✓ Buy order placed for Robin Hack
```

## Testing

Created test script: `test_mybids_verify.py`

**Results**:

```bash
$ python test_mybids_verify.py

Regular market endpoint: 53 players
My bids endpoint: 53 players (same - no filtering)

Your active bids:
  - Dominik Kohr
    Your bid: €9,281,213
    Market value: €8,554,114
    Bid vs MV: 8.5% over
  - Robin Hack
    Your bid: €7,226,114
    Market value: €6,721,967
    Bid vs MV: 7.5% over

✓ SUCCESS! Can identify active bids from market data
```

## Impact on Bot Behavior

### Before:

- ❌ Bot couldn't see active bids
- ❌ Budget calculations didn't account for pending bids
- ❌ Could place duplicate bids unintentionally
- ❌ Could run out of budget unexpectedly

### After:

- ✅ Bot aware of all active bids
- ✅ Shows all opportunities (including players we bid on)
- ✅ Allows re-bidding if smart bid > current bid + 5%
- ✅ Allows multiple simultaneous bids (flip + long-term)
- ✅ Budget accounts for total bid exposure
- ✅ Clear status: "already bid €X" or "increasing bid"
- ✅ Understands we don't know who's winning until auction ends

## Files Modified

1. **`rehoboam/kickbase_client.py`**

   - Added `get_my_bids()` method

1. **`rehoboam/api.py`**

   - Added `get_my_bids()` wrapper

1. **`rehoboam/auto_trader.py`**

   - Updated `run_profit_trading_session()` with bid checking
   - Updated `run_lineup_improvement_session()` with bid checking
   - Effective budget calculation in both methods

1. **Test Scripts Created**:

   - `test_my_bids_endpoint.py` - Endpoint discovery
   - `test_my_bids_endpoint2.py` - Additional endpoint testing
   - `test_mybids_verify.py` - Verification test

## Usage

```python
from rehoboam.api import KickbaseAPI

api = KickbaseAPI(email, password)
api.login()

league = api.get_leagues()[0]

# Get all your active bids
my_bids = api.get_my_bids(league)

for bid in my_bids:
    print(f"{bid.first_name} {bid.last_name}: €{bid.user_offer_price:,}")
```

## API Design Notes

The KICKBASE API follows a "fat endpoint" design where:

- `/market` returns ALL data (market players + your bids + offers)
- No dedicated filtered endpoints for subsets
- Client-side filtering is required
- This is actually efficient since you often need both market data AND bid status

**Important Limitation**:

- You DON'T know if you're the highest bidder
- You DON'T know what the highest bid is
- You only find out when the auction ends (player goes to winner or back to KICKBASE)
- This means the bot must be comfortable with uncertainty and can bid on many players simultaneously

## Next Steps

✅ **Week 1**: Bot now aware of active bids
⏳ **Week 2+**: Consider tracking bid outcomes for learning system

See `docs/LEARNING_SYSTEM_PROPOSAL.md` for future enhancements.
