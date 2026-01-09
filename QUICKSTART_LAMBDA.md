# Quick Start: Deploy to AWS Lambda in 10 Minutes

This is the fastest path to get your bot running on AWS for free.

## Step 1: Install AWS CLI (2 minutes)

```bash
# macOS
brew install awscli

# Or using pip
pip install awscli

# Configure with your credentials
aws configure
# You'll need: Access Key ID, Secret Access Key from AWS Console
# Get them: AWS Console → IAM → Users → Your User → Security Credentials → Create Access Key
```

## Step 2: Create Lambda Function via AWS Console (3 minutes)

1. Go to https://console.aws.amazon.com/lambda
1. Click **"Create function"**
1. Choose **"Author from scratch"**
1. Settings:
   - **Function name**: `rehoboam-trading-bot`
   - **Runtime**: Python 3.11
   - Click **"Create function"**
1. Go to **Configuration** tab → **General configuration** → **Edit**:
   - **Memory**: 512 MB
   - **Timeout**: 5 min 0 sec
   - Click **"Save"**

## Step 3: Add Your Credentials (2 minutes)

Still in Lambda console:

1. Go to **Configuration** tab → **Environment variables** → **Edit**
1. Add these variables:

```
KICKBASE_EMAIL = your.email@example.com
KICKBASE_PASSWORD = yourpassword
DRY_RUN = true
```

3. Click **"Save"**

⚠️ **Keep DRY_RUN=true** until you've tested!

## Step 4: Deploy Your Code (1 minute)

In your terminal, from the rehoboam directory:

```bash
# Make deployment script executable (first time only)
chmod +x deploy_lambda.sh

# Deploy
./deploy_lambda.sh
```

Wait for "Deployment complete!" message.

## Step 5: Test It (1 minute)

```bash
aws lambda invoke --function-name rehoboam-trading-bot output.json

# View results
cat output.json
```

You should see recommendations like:

```json
{
  "statusCode": 200,
  "body": {
    "league": "Your League",
    "buy_opportunities": 3,
    "recommendations": [...]
  }
}
```

## Step 6: Set Up Auto-Trading Schedule (2 minutes)

1. Go to https://console.aws.amazon.com/events/
1. Click **"Rules"** in left sidebar → **"Create rule"**
1. Settings:
   - **Name**: `rehoboam-schedule`
   - **Rule type**: Schedule
   - Click **"Continue in EventBridge Scheduler"** (or use schedule pattern)
1. Schedule:
   - **Schedule pattern**: Rate-based schedule
   - **Rate expression**: `6 hours` (or choose your frequency)
1. Target:
   - **Select a target**: Lambda function
   - **Function**: `rehoboam-trading-bot`
1. Click **"Create"**

**Done!** Your bot now runs automatically every 6 hours.

## What Happens Now?

- Every 6 hours, the bot:
  1. Logs into your Kickbase account
  1. Analyzes all market players
  1. Finds top 5 buy opportunities
  1. **In dry-run**: Logs recommendations (no trades)
  1. **When live**: Executes trades automatically

## View Execution Logs

```bash
# Stream live logs
aws logs tail /aws/lambda/rehoboam-trading-bot --follow

# View last hour
aws logs tail /aws/lambda/rehoboam-trading-bot --since 1h
```

## Going Live (When Ready)

Once you're confident in the recommendations:

1. Lambda Console → Configuration → Environment variables
1. Change `DRY_RUN` from `true` to `false`
1. Save

⚠️ **Bot will now execute REAL trades!** Start with conservative settings.

## Adjust Trading Settings (Optional)

Add these environment variables to fine-tune behavior:

```bash
MIN_VALUE_SCORE_TO_BUY = 55.0        # Higher = more selective (default: 50)
MAX_PLAYER_COST = 3000000            # Max €3M per player (default: 5M)
RESERVE_BUDGET = 2000000             # Keep €2M reserve (default: 1M)
MIN_SELL_PROFIT_PCT = 8.0            # Sell at 8% profit (default: 5%)
MAX_LOSS_PCT = -5.0                  # Stop loss at -5% (default: -3%)
```

## Update Bot Code

When you make changes locally:

```bash
# 1. Test locally
python lambda_handler.py

# 2. Deploy to Lambda
./deploy_lambda.sh

# 3. Test on Lambda
aws lambda invoke --function-name rehoboam-trading-bot output.json
```

## Costs

**Expected**: $0.00/month (free tier covers it)

Monitor at: https://console.aws.amazon.com/billing/

## Common Issues

**"Unable to import module"**: Run `./deploy_lambda.sh` again

**"Timeout"**: Increase timeout in Lambda Configuration → General configuration

**"No recommendations"**: Check logs with `aws logs tail /aws/lambda/rehoboam-trading-bot`

**Wrong credentials**: Update environment variables in Lambda Configuration

## Stop the Bot

To pause automated trading:

1. EventBridge Console → Rules
1. Select `rehoboam-schedule`
1. Click **"Disable"**

To resume, click **"Enable"**

______________________________________________________________________

**Need help?** See full documentation in `DEPLOYMENT.md`
