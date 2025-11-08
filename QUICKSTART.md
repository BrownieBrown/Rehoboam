# Rehoboam - Quick Start Guide

## 🚀 Get Started in 3 Steps

### 1. Configure Your Credentials

Your `.env` is already set up with your KICKBASE credentials.

### 2. Choose Your Deployment

#### Option A: Docker (Set & Forget - Runs Every 4 Hours)

```bash
# Build and start
docker-compose build
docker-compose up -d

# View logs
tail -f logs/trade.log
```

#### Option B: Manual Runs (You Control When)

```bash
# Activate environment
source venv/bin/activate

# Analyze market
rehoboam analyze

# Trade (dry-run mode)
rehoboam trade --max 5

# Trade live
rehoboam trade --live
```

### 3. Monitor Results

```bash
# Docker logs
docker-compose logs -f

# Or local logs
tail -f logs/trade.log
```

## 📊 What the Bot Does

**Every 4 hours (Docker mode):**
1. Login to KICKBASE
2. Fetch market players (KICKBASE sellers only)
3. Analyze opportunities (market value vs price)
4. Place bids on undervalued players
5. Protect your starting 11 + high performers

**Current Market Status:**
- 21 KICKBASE players available
- 33 user listings (filtered out)
- Budget: €2,892,600
- Squad: 11 players (all protected as starters)

## 🛡️ Safeguards Active

- ✅ Never sells starting 11
- ✅ Maintains minimum 11 players
- ✅ Protects high performers (50+ points)
- ✅ Only bids on KICKBASE listings
- ✅ Respects budget limits

## 🔧 Common Commands

```bash
# Check config
rehoboam config

# Test login
rehoboam login

# Analyze without trading
rehoboam analyze

# Trade (dry-run)
rehoboam trade --max 5

# Trade live
rehoboam trade --live

# View all options
rehoboam --help
```

## 📈 Trading Schedule (Docker)

- **Startup**: Immediately
- **00:00**: Midnight
- **04:00**: 4 AM
- **08:00**: 8 AM
- **12:00**: Noon
- **16:00**: 4 PM
- **20:00**: 8 PM

## ⚙️ Configuration (.env)

**Currently set:**
- Min buy value increase: 10%
- Min sell profit: 5%
- Max loss: -3%
- Max player cost: €5M
- Reserve budget: €1M
- Squad size: min 11
- Dry run: **TRUE** (safe mode)

## 🎯 Next Steps

1. **Test locally first**:
   ```bash
   source venv/bin/activate
   rehoboam analyze
   rehoboam trade --max 1  # Test with 1 trade
   ```

2. **When ready, deploy with Docker**:
   ```bash
   docker-compose up -d
   ```

3. **Monitor for a day**, then:
   - Set `DRY_RUN=false` in `.env` to enable live trading
   - `docker-compose restart` to apply changes

## ⚠️ Important Notes

- **Bidding system**: You place offers, highest bid wins
- **Won't know immediately**: Results come later when auction ends
- **4-hour cycle**: Bot continuously looks for new opportunities
- **Protected team**: Your starters are safe, won't be sold

## 📞 Support

Check these files for more info:
- `README.md` - Full documentation
- `DOCKER.md` - Docker deployment details
- `.env.example` - All configuration options
