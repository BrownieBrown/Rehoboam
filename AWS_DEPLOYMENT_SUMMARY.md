# AWS Deployment - Complete Summary

## 🎯 What You Get

Deploy your Rehoboam trading bot to AWS Lambda for:

- ✅ **$0/month** (stays in free tier forever)
- ✅ **Fully automated** trading (runs on schedule)
- ✅ **No server management** (serverless)
- ✅ **Scalable** (handles any league size)
- ✅ **Monitored** (CloudWatch logs and metrics)

## 📁 Files Created

I've created everything you need for AWS deployment:

### 1. Core Lambda Files

- **`lambda_handler.py`** - AWS Lambda function that runs analyze + auto-trade
  - Handles scheduled execution via EventBridge
  - Uses environment variables for credentials
  - Returns execution summary with trades and recommendations
  - Includes local test mode (`python lambda_handler.py`)

### 2. Deployment Tools

- **`deploy_lambda.sh`** - One-command deployment script

  - Packages code and dependencies into ZIP
  - Uploads to AWS Lambda
  - Handles updates automatically
  - Cleans up build artifacts

- **`requirements.txt`** - Python dependencies for Lambda

  - Extracted from your pyproject.toml
  - Optimized for Lambda (excludes boto3/botocore)

### 3. Documentation

- **`QUICKSTART_LAMBDA.md`** - 10-minute quick start guide

  - Step-by-step for absolute beginners
  - Get running in 10 minutes
  - No AWS experience needed

- **`DEPLOYMENT.md`** - Complete deployment guide

  - Detailed explanations for each step
  - Troubleshooting section
  - Security best practices
  - Cost monitoring
  - Alternative deployment options

- **`AWS_DEPLOYMENT_SUMMARY.md`** - This file!

## 🚀 Quick Start (10 Minutes)

Follow **`QUICKSTART_LAMBDA.md`** for the fastest path:

1. **Install AWS CLI** (2 min)
1. **Create Lambda function** in AWS Console (3 min)
1. **Add credentials** as environment variables (2 min)
1. **Deploy code** with `./deploy_lambda.sh` (1 min)
1. **Test it** (1 min)
1. **Schedule automation** in EventBridge (2 min)

**Total: 10 minutes from zero to automated trading!**

## 📊 How It Works

```
EventBridge Scheduler (Every 6 hours)
          ↓
   Lambda Function Executes
          ↓
   ┌──────────────────────┐
   │ 1. Login to Kickbase │
   │ 2. Fetch market data │
   │ 3. Analyze players   │
   │ 4. Find opportunities│
   │ 5. Execute trades    │  ← Only if DRY_RUN=false
   │ 6. Log results       │
   └──────────────────────┘
          ↓
   CloudWatch Logs (Monitor)
```

## 💰 Cost Breakdown

**Your expected monthly usage:**

- **Executions**: 120/month (every 6 hours)
- **Duration**: ~2 minutes per execution
- **Memory**: 512 MB
- **Compute**: 7,200 GB-seconds/month

**AWS Free Tier:**

- 1,000,000 requests/month (you use: 120)
- 400,000 GB-seconds/month (you use: 7,200)
- EventBridge: Free
- CloudWatch Logs: 5GB free (you use: \<100 MB)

**Your cost: $0.00/month** ✅

Even running every hour (720 executions/month) = **still $0.00**!

## 🔒 Security

Your Kickbase credentials are stored as Lambda environment variables:

- ✅ Not in code (never committed to git)
- ✅ Encrypted at rest by AWS
- ✅ Only accessible by your Lambda function
- ✅ Can be rotated anytime

For extra security, upgrade to **AWS Secrets Manager** (+$0.40/month):

```bash
aws secretsmanager create-secret \
  --name rehoboam/kickbase \
  --secret-string '{"email":"your@email.com","password":"yourpass"}'
```

## 📈 Monitoring

### View Logs (Real-time)

```bash
aws logs tail /aws/lambda/rehoboam-trading-bot --follow
```

### Check Last Execution

```bash
aws lambda invoke --function-name rehoboam-trading-bot output.json
cat output.json | jq .
```

### Lambda Metrics

AWS Console → Lambda → rehoboam-trading-bot → Monitor

- Invocations
- Duration
- Errors
- Success rate

### Set Up Alerts

CloudWatch → Alarms → Create:

- Alert on errors
- Alert on high cost
- Email notifications via SNS

## 🎛️ Configuration

All trading parameters are controlled via Lambda environment variables:

| Variable                 | Default   | Description                     |
| ------------------------ | --------- | ------------------------------- |
| `KICKBASE_EMAIL`         | -         | Your account email (required)   |
| `KICKBASE_PASSWORD`      | -         | Your password (required)        |
| `DRY_RUN`                | `true`    | Set to `false` for real trading |
| `MIN_VALUE_SCORE_TO_BUY` | `50.0`    | Only buy players scoring 50+    |
| `MAX_PLAYER_COST`        | `5000000` | Max €5M per player              |
| `RESERVE_BUDGET`         | `1000000` | Keep €1M in reserve             |
| `MIN_SELL_PROFIT_PCT`    | `5.0`     | Sell at 5% profit               |
| `MAX_LOSS_PCT`           | `-3.0`    | Stop loss at -3%                |

Change anytime in: Lambda Console → Configuration → Environment variables

## 🔄 Update Process

When you improve the bot locally:

```bash
# 1. Test locally
python lambda_handler.py

# 2. Deploy to Lambda
./deploy_lambda.sh

# 3. Verify on AWS
aws lambda invoke --function-name rehoboam-trading-bot output.json
```

**No downtime!** Lambda updates in seconds.

## 📅 Scheduling Options

EventBridge cron expressions for different strategies:

### Conservative (Every 6 hours)

```
cron(0 */6 * * ? *)
```

Runs at: 00:00, 06:00, 12:00, 18:00 UTC

### Active (Every 4 hours)

```
cron(0 */4 * * ? *)
```

Runs at: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC

### Strategic Times

```
cron(0 9,15,21 * * ? *)
```

Runs at: 9 AM, 3 PM, 9 PM UTC (after work hours)

### Weekdays Only

```
cron(0 12 * * MON-FRI *)
```

Runs at: Noon, Monday-Friday only

### Before Price Changes

```
cron(0 21 * * ? *)
```

Runs at: 9 PM daily (3 hours before 22:00 price adjustments)

## 🛑 Pause/Resume Trading

### Pause (Keep bot deployed but stop executions)

```bash
aws events disable-rule --name rehoboam-schedule
```

### Resume

```bash
aws events enable-rule --name rehoboam-schedule
```

### Delete Everything

```bash
# Delete EventBridge rule
aws events remove-targets --rule rehoboam-schedule --ids 1
aws events delete-rule --name rehoboam-schedule

# Delete Lambda function
aws lambda delete-function --function-name rehoboam-trading-bot
```

## 🐛 Common Issues

### "Unable to import module"

**Solution**: Redeploy

```bash
./deploy_lambda.sh
```

### "Task timed out"

**Solution**: Increase timeout

```bash
aws lambda update-function-configuration \
  --function-name rehoboam-trading-bot \
  --timeout 600
```

### No buy recommendations

**Check**:

1. Is DRY_RUN=true? (Expected for dry run)
1. Are MIN_VALUE_SCORE_TO_BUY settings too strict?
1. Check CloudWatch logs for errors

### Database not persisting

**Expected**: Lambda's `/tmp` is ephemeral (resets between invocations)
**Impact**: Historical data re-fetches from API with caching (minimal)
**Fix if needed**: Use Amazon EFS or S3 for persistent storage (see DEPLOYMENT.md)

## 📚 Next Steps

1. **Read**: `QUICKSTART_LAMBDA.md` - Get deployed in 10 minutes
1. **Test**: Run in DRY_RUN mode for a few days
1. **Monitor**: Check CloudWatch logs to verify recommendations
1. **Go Live**: Set DRY_RUN=false when satisfied
1. **Optimize**: Adjust MIN_VALUE_SCORE_TO_BUY based on results

## 🎓 Advanced Topics

See `DEPLOYMENT.md` for:

- Using AWS Secrets Manager for credentials
- Setting up persistent storage (EFS/S3)
- Creating custom IAM policies
- Multi-region deployment
- CI/CD integration with GitHub Actions
- Lambda Layers for large dependencies

## 💡 Tips

1. **Start conservative**: DRY_RUN=true, MIN_VALUE_SCORE_TO_BUY=55
1. **Monitor closely**: Check logs daily for first week
1. **Adjust schedule**: Run more frequently during transfer windows
1. **Use alerts**: Get notified of errors via CloudWatch alarms
1. **Review trades**: Check output.json after each execution
1. **Backup strategy**: Keep some manual trading to diversify

## 🏆 Benefits vs Running Locally

| Feature             | Local                 | AWS Lambda        |
| ------------------- | --------------------- | ----------------- |
| **Cost**            | $0 (your computer)    | $0 (free tier)    |
| **Uptime**          | Must keep computer on | 99.99% uptime     |
| **Automation**      | Cron job              | EventBridge       |
| **Monitoring**      | Manual                | CloudWatch        |
| **Scaling**         | Limited               | Automatic         |
| **Maintenance**     | You manage            | AWS manages       |
| **Access anywhere** | No                    | Yes (AWS Console) |

## 📞 Support

- **AWS Issues**: Check CloudWatch logs first
- **Bot Issues**: Test locally with `python lambda_handler.py`
- **Questions**: See troubleshooting in `DEPLOYMENT.md`

______________________________________________________________________

**Ready to deploy?** → Start with `QUICKSTART_LAMBDA.md` 🚀
