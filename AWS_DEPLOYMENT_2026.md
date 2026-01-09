# AWS Lambda Deployment - 2026 Edition

## With Competitive Intelligence & Activity Feed Learning

**🎉 NEW**: Your bot now includes competitive intelligence that learns from ALL league transfers!

______________________________________________________________________

## 🆓 Yes, It's Still 100% FREE!

Deploy your bot to AWS Lambda with:

- ✅ **$0/month forever** (free tier covers typical usage)
- ✅ **Competitive intelligence** (learns from Eduard, Chris, and all competitors!)
- ✅ **Activity feed learning** (300+ transfers/month of data)
- ✅ **Smart bidding** (9-15% overbid vs old 2-6%)
- ✅ **24/7 automated trading**
- ✅ **No server management**

______________________________________________________________________

## 🚀 Quick Start (15 Minutes)

### **Step 1: Install AWS CLI** (2 min)

```bash
# macOS
brew install awscli

# Or using pip
pip install awscli

# Configure
aws configure
# Enter: Access Key ID, Secret Access Key, Region (us-east-1)
# Get keys from: AWS Console → IAM → Users → Your User → Security Credentials
```

### **Step 2: Create Lambda Function** (3 min)

1. Go to https://console.aws.amazon.com/lambda
1. Click **"Create function"**
1. Choose **"Author from scratch"**
1. Settings:
   - **Function name**: `rehoboam-trading-bot`
   - **Runtime**: Python 3.11
   - **Architecture**: x86_64
1. Click **"Create function"**
1. Go to **Configuration** → **General configuration** → **Edit**:
   - **Memory**: 1024 MB (increased for competitive intelligence)
   - **Timeout**: 10 min 0 sec (was 5 min, now needs more for activity feed sync)
   - **Ephemeral storage**: 1024 MB (for learning database)
1. Click **"Save"**

### **Step 3: Add Environment Variables** (2 min)

**Configuration** → **Environment variables** → **Edit**:

```
KICKBASE_EMAIL = your.email@example.com
KICKBASE_PASSWORD = yourpassword
DRY_RUN = true
MIN_VALUE_SCORE_TO_BUY = 50.0
MAX_PLAYER_COST = 5000000
RESERVE_BUDGET = 1000000
```

⚠️ **Keep DRY_RUN=true** until you've tested!

### **Step 4: Deploy Code** (2 min)

```bash
# From rehoboam directory
./deploy_lambda.sh
```

Wait for "Deployment complete!" message.

### **Step 5: Test It** (1 min)

```bash
aws lambda invoke --function-name rehoboam-trading-bot output.json
cat output.json | jq .
```

Expected output:

```json
{
  "statusCode": 200,
  "body": {
    "league": "PUMARUDEL 25/26",
    "dry_run": true,
    "duration_seconds": 45.2,
    "competitive_intelligence": "ENABLED",
    "profit_trades": [],
    "lineup_trades": [],
    "total_spent": 0,
    "total_earned": 0,
    "net_change": 0
  }
}
```

### **Step 6: Set Up Schedule** (3 min)

1. Go to https://console.aws.amazon.com/events/
1. Click **"Create rule"**
1. Settings:
   - **Name**: `rehoboam-schedule`
   - **Rule type**: Schedule
   - **Schedule pattern**: `cron(0 */6 * * ? *)` (every 6 hours)
1. **Target**: Lambda function → `rehoboam-trading-bot`
1. Click **"Create"**

**Done!** Bot runs automatically every 6 hours with competitive intelligence. 🎯

______________________________________________________________________

## 🆕 What's New in 2026 Edition?

### **1. Activity Feed Learning**

- Bot syncs league transfer activity at start of each session
- Learns from ALL transfers, not just your bids
- **300+ transfers/month** of learning data (vs 100 before)

### **2. Competitive Intelligence**

- Tracks competitor behavior (Eduard: €18M avg, Very Aggressive)
- Identifies league competitiveness (Your league: €11.6M avg = Competitive)
- Adjusts bids based on competition level

### **3. Smart Bidding Evolution**

- **Before**: Hardcoded 10% overbid
- **Now**:
  - Base: 10%
  - League competitive: +5%
  - Hot player demand: +0-8%
  - **Total: 9-15% competitive bidding**

### **4. Full AutoTrader Integration**

- Uses same logic as local `rehoboam auto` command
- Squad optimization, profit trading, lineup improvements
- All with competitive intelligence enabled

______________________________________________________________________

## 📊 CloudWatch Logs - What to Expect

You'll now see in logs:

```
[INFO] Starting Rehoboam trading bot Lambda execution with competitive intelligence
[INFO] Logging in as your.email@example.com
[INFO] Processing league: PUMARUDEL 25/26
[INFO] Initializing trading components with competitive intelligence
[INFO] Auto-trading mode: DRY RUN

[INFO] Syncing league activity feed...
[INFO] ✓ Synced: 2 new transfers, 5 new market values

[INFO] 🤖 Auto-Trading: Profit Opportunities
[INFO] Active bids: 0
[INFO] Found 3 profit opportunities
[INFO] Smart Bid for Bernardo: €15,240,928 +15%
[INFO]   ⚡ Competitive league | Good value | confident

[INFO] 🤖 Auto-Trading: Lineup Improvements
[INFO] Found 5 buy opportunities
[INFO] Smart Bid for Nicolas Seiwald: €23,846,730 +15%
[INFO]   ⚡ Competitive league | 🎯 HOT PLAYER | Exceptional value

[INFO] Session completed: 0 profit trades, 0 lineup trades (DRY RUN)
[INFO] Net change: €0
```

______________________________________________________________________

## 💰 Updated Cost Estimate

### **Old Handler (Simple)**

- 120 executions/month × 2 min × 512 MB = 7,200 GB-seconds
- **Cost**: $0.00

### **New Handler (With Competitive Intelligence)**

- 120 executions/month × 3 min × 1024 MB = 21,600 GB-seconds
- **Free tier**: 400,000 GB-seconds/month
- **Your cost**: $0.00 ✅

**Still free!** Even with 3x more compute, you're well within free tier.

______________________________________________________________________

## 🎯 Monitoring Competitive Intelligence

### **View Learning Stats**

After a few runs, check the database:

```bash
# Download the database from Lambda (if using EFS)
# Or check local stats after syncing

python -c "
from rehoboam.activity_feed_learner import ActivityFeedLearner
learner = ActivityFeedLearner()
learner.display_league_stats()
learner.display_competitor_analysis()
"
```

### **CloudWatch Insights Queries**

Query logs for competitive intelligence:

```
fields @timestamp, @message
| filter @message like /Synced: /
| sort @timestamp desc
| limit 20
```

```
fields @timestamp, @message
| filter @message like /Competitive league/
| sort @timestamp desc
| limit 20
```

______________________________________________________________________

## 🔧 Configuration Tweaks

### **Adjust Aggressiveness**

If bot is too conservative:

```bash
# Environment variables:
MIN_VALUE_SCORE_TO_BUY = 45.0  # Lower = more opportunities (was 50)
MAX_PLAYER_COST = 10000000     # Higher = can buy expensive players (was 5M)
```

If bot is too aggressive:

```bash
MIN_VALUE_SCORE_TO_BUY = 60.0  # Higher = more selective (was 50)
MAX_PLAYER_COST = 3000000      # Lower = budget players only (was 5M)
```

### **Adjust Schedule**

For different trading frequencies:

**Very Active (Every 3 hours)**

```
cron(0 */3 * * ? *)
```

**Strategic Times (After work)**

```
cron(0 9,15,21 * * ? *)
```

Runs at: 9 AM, 3 PM, 9 PM UTC

**Before Market Changes (Daily at 9 PM)**

```
cron(0 21 * * ? *)
```

3 hours before 22:00 price adjustments

______________________________________________________________________

## 🚦 Going Live

Once satisfied with dry run results:

1. Lambda Console → Configuration → Environment variables
1. Edit `DRY_RUN` → Change to `false`
1. **Save**

⚠️ **Bot will now execute REAL trades with competitive intelligence!**

Expected behavior:

- Bids 9-15% over asking (competitive)
- Learns from Eduard and other competitors
- Adjusts based on league activity
- Tracks which bids win/lose

______________________________________________________________________

## 📈 Performance Expectations

### **Old Bot (Before Competitive Intelligence)**

- Win rate: **28.6%** (losing most auctions)
- Overbid: **2-6%** (too conservative)
- Learning data: **56 auctions** (your bids only)

### **New Bot (With Competitive Intelligence)**

- Expected win rate: **40-50%+** (competitive)
- Overbid: **9-15%** (matches league)
- Learning data: **300+ transfers/month** (entire league)

______________________________________________________________________

## 🛠️ Troubleshooting

### **"Task timed out after 600 seconds"**

Increase timeout:

```bash
aws lambda update-function-configuration \
  --function-name rehoboam-trading-bot \
  --timeout 900 \
  --region us-east-1
```

### **"Memory limit exceeded"**

Increase memory:

```bash
aws lambda update-function-configuration \
  --function-name rehoboam-trading-bot \
  --memory-size 1536 \
  --region us-east-1
```

### **Not seeing competitive intelligence in logs**

Check that the new lambda_handler.py was deployed:

```bash
./deploy_lambda.sh
aws lambda invoke --function-name rehoboam-trading-bot output.json
cat output.json | jq '.body' | jq -r . | jq .
```

Look for: `"competitive_intelligence": "ENABLED"`

______________________________________________________________________

## 🔐 Security Best Practices

1. **Never commit credentials** to git
1. **Use secrets manager** for production (optional, +$0.40/month):
   ```bash
   aws secretsmanager create-secret \
     --name rehoboam/kickbase \
     --secret-string '{"email":"your@email.com","password":"yourpass"}'
   ```
1. **Set up billing alerts**:
   - Billing Console → Billing Preferences → Enable alerts
   - CloudWatch → Alarms → Create: Billing > $5
1. **Enable CloudTrail** for audit logs

______________________________________________________________________

## 📊 Compare Deployment Options

| Feature                      | AWS Lambda (Recommended) | EC2 t2.micro              | Local          |
| ---------------------------- | ------------------------ | ------------------------- | -------------- |
| **Cost**                     | $0 (free tier forever)   | $0 (12 months) then $8/mo | $0 (your PC)   |
| **Competitive Intelligence** | ✅ Yes                   | ✅ Yes                    | ✅ Yes         |
| **Uptime**                   | 99.99%                   | 99.5%                     | Requires PC on |
| **Maintenance**              | None (AWS manages)       | You manage updates        | You manage     |
| **Scaling**                  | Automatic                | Manual                    | Limited        |
| **Setup Time**               | 15 minutes               | 1 hour                    | 5 minutes      |
| **Monitoring**               | CloudWatch (free)        | Manual                    | Manual         |

**Winner**: AWS Lambda for 99% of use cases.

______________________________________________________________________

## 🎓 Advanced Features

### **Persistent Database (Optional)**

For long-term learning history, use EFS:

1. Create EFS file system (5GB free tier)
1. Attach to Lambda
1. Update handler:
   ```python
   os.environ["REHOBOAM_DB_PATH"] = "/mnt/efs"
   ```

**Cost**: $0 for 5GB (free tier)

### **Multi-League Support**

To trade in multiple leagues, modify handler to iterate:

```python
for league in leagues:
    session_result = auto_trader.run_full_session(league)
```

### **Slack/Discord Notifications**

Add webhook notifications in handler:

```python
# After session completes
import requests

requests.post(
    WEBHOOK_URL, json={"text": f"Trade session complete: {net_change:,} profit"}
)
```

______________________________________________________________________

## 📞 Support

**Issues with deployment?**

1. Check CloudWatch logs: `aws logs tail /aws/lambda/rehoboam-trading-bot --follow`
1. Test locally first: `python lambda_handler.py`
1. Verify credentials in environment variables
1. Check free tier limits: https://console.aws.amazon.com/billing/

**Bot not winning auctions?**

- This is expected in competitive leagues
- Bot will learn and adjust over time (needs 10-20 more auctions)
- Consider increasing `MIN_VALUE_SCORE_TO_BUY` to be more selective

______________________________________________________________________

## 🎉 Summary

**Before deploying:**

- ❌ No competitive intelligence
- ❌ Bidding 2-6% (losing to Eduard's 36%+ bids)
- ❌ Learning from ~100 auctions/month only

**After deploying with 2026 updates:**

- ✅ Full competitive intelligence
- ✅ Bidding 9-15% (competitive with league)
- ✅ Learning from 300+ transfers/month
- ✅ Tracking Eduard and all competitors
- ✅ Adjusting for league competitiveness
- ✅ **Still $0/month!**

______________________________________________________________________

**Ready to deploy?** Follow the Quick Start above! 🚀

For more details, see:

- `QUICKSTART_LAMBDA.md` - Step-by-step beginner guide
- `DEPLOYMENT.md` - Complete deployment documentation
- `AWS_DEPLOYMENT_SUMMARY.md` - Overview of all files
