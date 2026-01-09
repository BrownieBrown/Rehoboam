# AWS Lambda Deployment Guide for Rehoboam Trading Bot

This guide will help you deploy the Rehoboam trading bot to AWS Lambda for **FREE** automated trading.

## Why AWS Lambda?

- **Cost**: FREE for most use cases (1M requests/month free, 400K GB-seconds compute)
- **Serverless**: No server management, automatic scaling
- **Scheduled Execution**: Run analyze + trade on autopilot (e.g., every 6 hours)
- **Pay Only What You Use**: If you run every 6 hours = 120 executions/month = $0.00

## Cost Estimation

**Assumptions:**

- Run 4x per day (every 6 hours) = 120 executions/month
- 2 minutes per execution
- 512 MB memory
- Storage in Lambda /tmp (ephemeral, free)

**Monthly Cost:**

- Compute: 120 requests × 120 seconds × 512 MB = 7,200 GB-seconds
- Free tier: 400,000 GB-seconds/month
- **Your cost: $0.00** ✅

Even if you run every hour (720 executions/month), you'd still be in the free tier!

## Prerequisites

1. **AWS Account** (create at https://aws.amazon.com)

   - Free tier includes 12 months of free services
   - Credit card required but won't be charged for free tier usage

1. **AWS CLI** installed and configured

   ```bash
   # Install AWS CLI
   brew install awscli  # macOS
   # or: pip install awscli

   # Configure with your AWS credentials
   aws configure
   # Enter: Access Key ID, Secret Access Key, Region (e.g., us-east-1)
   ```

1. **Python 3.11** (matches Lambda runtime)

## Deployment Steps

### Step 1: Create IAM Role for Lambda

Lambda needs permission to execute and write logs.

1. Go to AWS Console → IAM → Roles → Create Role
1. Select "AWS Service" → "Lambda"
1. Attach policy: `AWSLambdaBasicExecutionRole` (allows CloudWatch logging)
1. Name: `rehoboam-lambda-execution-role`
1. Copy the Role ARN (e.g., `arn:aws:iam::123456789:role/rehoboam-lambda-execution-role`)

### Step 2: Create Lambda Function

**Option A: Using AWS Console (Easiest)**

1. Go to AWS Console → Lambda → Create Function
1. Choose "Author from scratch"
1. Configuration:
   - **Function name**: `rehoboam-trading-bot`
   - **Runtime**: Python 3.11
   - **Architecture**: x86_64
   - **Execution role**: Use existing role → `rehoboam-lambda-execution-role`
1. Click "Create function"
1. Under "Configuration" → "General configuration":
   - **Memory**: 512 MB
   - **Timeout**: 5 minutes (300 seconds)
1. Click "Save"

**Option B: Using AWS CLI**

```bash
aws lambda create-function \
  --function-name rehoboam-trading-bot \
  --runtime python3.11 \
  --handler lambda_handler.lambda_handler \
  --memory-size 512 \
  --timeout 300 \
  --role arn:aws:iam::YOUR_ACCOUNT_ID:role/rehoboam-lambda-execution-role \
  --zip-file fileb://rehoboam-lambda.zip \
  --region us-east-1
```

### Step 3: Deploy Code to Lambda

Run the deployment script:

```bash
./deploy_lambda.sh
```

This will:

1. Package all code and dependencies
1. Create a ZIP file (~10-20 MB)
1. Upload to your Lambda function

### Step 4: Configure Environment Variables

Your Kickbase credentials must be stored as environment variables (NOT in code).

1. Go to Lambda Console → Your function → Configuration → Environment variables
1. Add the following:

| Key                      | Value                  | Required                                 |
| ------------------------ | ---------------------- | ---------------------------------------- |
| `KICKBASE_EMAIL`         | your.email@example.com | ✅ Yes                                   |
| `KICKBASE_PASSWORD`      | yourpassword           | ✅ Yes                                   |
| `DRY_RUN`                | `true`                 | ✅ Yes (set to `false` for real trading) |
| `MIN_VALUE_SCORE_TO_BUY` | `50.0`                 | Optional (default: 50)                   |
| `MAX_PLAYER_COST`        | `5000000`              | Optional (default: 5M)                   |
| `RESERVE_BUDGET`         | `1000000`              | Optional (default: 1M)                   |
| `MIN_SELL_PROFIT_PCT`    | `5.0`                  | Optional (default: 5%)                   |
| `MAX_LOSS_PCT`           | `-3.0`                 | Optional (default: -3%)                  |

⚠️ **IMPORTANT**: Start with `DRY_RUN=true` to test without real trades!

### Step 5: Test the Function

Test manually before setting up automation:

```bash
aws lambda invoke \
  --function-name rehoboam-trading-bot \
  --region us-east-1 \
  output.json

# View results
cat output.json | jq .
```

Expected output:

```json
{
  "statusCode": 200,
  "body": {
    "league": "Your League Name",
    "budget": 15000000,
    "players_analyzed": 150,
    "buy_opportunities": 5,
    "dry_run": true,
    "trades_executed": [],
    "recommendations": [
      {
        "player": "John Doe",
        "position": "ST",
        "price": 3500000,
        "value_score": 78.5,
        "trend": "rising",
        "trend_change": 12.3
      }
    ]
  }
}
```

Check CloudWatch logs:

```bash
aws logs tail /aws/lambda/rehoboam-trading-bot --follow --region us-east-1
```

### Step 6: Set Up Automated Schedule

Use EventBridge (formerly CloudWatch Events) to run the bot automatically.

**Option A: Using AWS Console**

1. Go to EventBridge → Rules → Create Rule
1. **Name**: `rehoboam-schedule`
1. **Rule type**: Schedule
1. **Schedule pattern**: Choose one:
   - **Every 6 hours**: `cron(0 */6 * * ? *)`
   - **Every 4 hours**: `cron(0 */4 * * ? *)`
   - **Every day at 9 AM**: `cron(0 9 * * ? *)`
   - **Custom**: Use cron expression builder
1. **Target**: AWS Lambda function
1. **Function**: `rehoboam-trading-bot`
1. **Create rule**

**Option B: Using AWS CLI**

```bash
# Create rule
aws events put-rule \
  --name rehoboam-schedule \
  --schedule-expression "cron(0 */6 * * ? *)" \
  --description "Run Rehoboam trading bot every 6 hours" \
  --region us-east-1

# Add Lambda as target
aws events put-targets \
  --rule rehoboam-schedule \
  --targets "Id"="1","Arn"="arn:aws:lambda:us-east-1:YOUR_ACCOUNT_ID:function:rehoboam-trading-bot" \
  --region us-east-1

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission \
  --function-name rehoboam-trading-bot \
  --statement-id rehoboam-eventbridge \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:us-east-1:YOUR_ACCOUNT_ID:rule/rehoboam-schedule \
  --region us-east-1
```

**Cron Expression Guide:**

AWS uses 6-field cron: `cron(Minutes Hours Day-of-month Month Day-of-week Year)`

Examples:

- `cron(0 */6 * * ? *)` - Every 6 hours
- `cron(0 9,15,21 * * ? *)` - 9 AM, 3 PM, 9 PM daily
- `cron(0 12 * * MON-FRI *)` - Noon on weekdays only
- `cron(0 0 * * ? *)` - Midnight daily

### Step 7: Monitor Execution

**CloudWatch Logs:**

```bash
# Stream logs in real-time
aws logs tail /aws/lambda/rehoboam-trading-bot --follow

# View recent executions
aws logs tail /aws/lambda/rehoboam-trading-bot --since 1h
```

**Lambda Metrics (in AWS Console):**

- Lambda → Your function → Monitor
- View: Invocations, Duration, Errors, Success rate

**Set Up Alerts (Optional):**

1. CloudWatch → Alarms → Create Alarm
1. Metric: Lambda → Errors → rehoboam-trading-bot
1. Condition: Errors > 0
1. Action: Send SNS notification to your email

## Going Live: Disable Dry Run

Once you've tested thoroughly and are satisfied with the recommendations:

1. Lambda Console → Configuration → Environment variables
1. Edit `DRY_RUN` → Change to `false`
1. **Save**

⚠️ **WARNING**: The bot will now execute REAL trades! Monitor closely at first.

## Troubleshooting

### Issue: "Unable to import module 'lambda_handler'"

**Solution**: Dependencies not packaged correctly

```bash
# Redeploy with correct dependencies
./deploy_lambda.sh
```

### Issue: "Task timed out after 300.00 seconds"

**Solution**: Increase timeout

```bash
aws lambda update-function-configuration \
  --function-name rehoboam-trading-bot \
  --timeout 600 \
  --region us-east-1
```

### Issue: Package too large (>50 MB)

**Solution**: Use Lambda Layers for dependencies

```bash
# Create layer for dependencies
mkdir python
pip install -r requirements.txt -t python/
zip -r dependencies-layer.zip python/

# Upload layer
aws lambda publish-layer-version \
  --layer-name rehoboam-dependencies \
  --zip-file fileb://dependencies-layer.zip \
  --compatible-runtimes python3.11

# Attach to function (use layer ARN from output)
aws lambda update-function-configuration \
  --function-name rehoboam-trading-bot \
  --layers arn:aws:lambda:us-east-1:ACCOUNT:layer:rehoboam-dependencies:1
```

### Issue: Database not persisting between runs

This is expected with `/tmp` storage. For persistent storage:

**Option 1: Amazon EFS** (costs ~$0.30/GB/month)

- Create EFS file system
- Mount to Lambda at `/mnt/efs`
- Update `lambda_handler.py`: `db_path = Path("/mnt/efs/player_history.db")`

**Option 2: Amazon S3** (costs ~$0.023/GB/month)

- Download DB from S3 at start
- Upload DB to S3 at end
- Add boto3 code in handler

For most use cases, `/tmp` is fine since historical data re-fetches from API with caching.

## Updating the Bot

When you make code changes locally:

```bash
# Test locally first
python lambda_handler.py

# Deploy to Lambda
./deploy_lambda.sh

# Test on Lambda
aws lambda invoke --function-name rehoboam-trading-bot output.json
```

## Security Best Practices

1. **Never commit credentials** - Use environment variables only
1. **Enable CloudTrail** - Audit all API calls
1. **Use secrets manager** (advanced):
   ```bash
   # Store credentials in AWS Secrets Manager
   aws secretsmanager create-secret \
     --name rehoboam/kickbase \
     --secret-string '{"email":"your@email.com","password":"yourpass"}'

   # Update Lambda to fetch from Secrets Manager (costs $0.40/month)
   ```
1. **Restrict IAM role** - Only grant necessary permissions

## Cost Monitoring

Check your AWS costs:

```bash
aws ce get-cost-and-usage \
  --time-period Start=2024-01-01,End=2024-01-31 \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=SERVICE
```

Set up billing alerts:

1. Billing Console → Billing Preferences
1. Enable "Receive Billing Alerts"
1. CloudWatch → Alarms → Create Alarm
1. Metric: Billing → Total Estimated Charge
1. Condition: > $5 (or your threshold)

## Alternative: EC2 (Not Recommended)

If you prefer a traditional server approach:

**EC2 t2.micro** (750 hours/month free for 12 months)

- Launch Ubuntu instance
- Install Python, clone repo
- Set up cron job: `0 */6 * * * cd /home/ubuntu/rehoboam && python -m rehoboam.cli analyze --auto-trade`
- **Downside**: Must manage server, costs $8-10/month after 12 months

## Support

- Check CloudWatch logs for errors
- Test locally with `python lambda_handler.py`
- Review AWS Lambda documentation: https://docs.aws.amazon.com/lambda/

______________________________________________________________________

**Summary**: Deploy once, trade automatically forever for **$0/month**! 🚀
