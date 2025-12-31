# Deploying Rehoboam to AWS (Free)

This guide shows you how to deploy your trading bot to AWS Lambda so it runs automatically on a schedule **completely free**.

## Option 1: AWS Lambda (Recommended - Free Forever)

### Prerequisites

1. **AWS Account** (free tier)

1. **AWS CLI** installed and configured

   ```bash
   # Install AWS CLI
   brew install awscli  # macOS

   # Configure with your credentials
   aws configure
   ```

1. **Create IAM Role** for Lambda:

   - Go to AWS Console → IAM → Roles
   - Create role → Lambda
   - Add policies: `AWSLambdaBasicExecutionRole`
   - Note the Role ARN

### Deployment Steps

1. **Update the deployment script** with your details:

   ```bash
   # Edit deploy/deploy-lambda.sh
   # Replace YOUR_ACCOUNT_ID with your AWS account ID
   # Update environment variables with your Kickbase credentials
   ```

1. **Make script executable**:

   ```bash
   chmod +x deploy/deploy-lambda.sh
   ```

1. **Deploy**:

   ```bash
   ./deploy/deploy-lambda.sh
   ```

1. **Done!** Your bot now runs automatically:

   - **Morning**: 10:30 AM CET (after market values update)
   - **Evening**: 6:00 PM CET

### SQLite Database Handling

Lambda is stateless, so the SQLite learning database needs special handling:

**Option A: Use S3 for persistent storage** (recommended)

```python
# Download DB from S3 at start, upload at end
import boto3

s3 = boto3.client("s3")
s3.download_file("your-bucket", "bid_learning.db", "/tmp/bid_learning.db")
# ... run bot ...
s3.upload_file("/tmp/bid_learning.db", "your-bucket", "bid_learning.db")
```

**Option B: Use DynamoDB instead of SQLite**

- More complex but fully serverless
- Requires code changes to use DynamoDB

**Option C: Disable learning** (simplest for now)

- Bot uses default settings without learning
- Still works fine, just doesn't adapt over time

### Cost

- **Lambda**: 1M requests/month FREE (you'll use ~60/month)
- **EventBridge**: FREE
- **S3**: First 5GB FREE (database is \<1MB)
- **Total**: $0.00/month ✅

______________________________________________________________________

## Option 2: GitHub Actions (Easier Alternative - Also Free)

If AWS seems complicated, GitHub Actions is simpler:

### Setup

1. **Create `.github/workflows/trading-bot.yml`**:

   ```yaml
   name: Trading Bot

   on:
     schedule:
       # Morning: 10:30 AM CET (8:30 UTC)
       - cron: '30 8 * * *'
       # Evening: 6:00 PM CET (16:00 UTC)
       - cron: '0 16 * * *'
     workflow_dispatch:  # Allow manual trigger

   jobs:
     trade:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v3

         - name: Set up Python
           uses: actions/setup-python@v4
           with:
             python-version: '3.11'

         - name: Install dependencies
           run: |
             pip install -r requirements.txt

         - name: Run trading bot
           env:
             KICKBASE_EMAIL: ${{ secrets.KICKBASE_EMAIL }}
             KICKBASE_PASSWORD: ${{ secrets.KICKBASE_PASSWORD }}
           run: |
             python -m rehoboam.cli auto --league 0
   ```

1. **Add secrets** to your GitHub repo:

   - Settings → Secrets → New repository secret
   - Add `KICKBASE_EMAIL`
   - Add `KICKBASE_PASSWORD`

1. **Push to GitHub** - it will run automatically!

**Pros:**

- ✅ Completely free (2000 minutes/month for private repos)
- ✅ Very easy to set up
- ✅ Easy to check logs

**Cons:**

- ❌ SQLite database won't persist (resets each run)
- ❌ Need to push code to GitHub

______________________________________________________________________

## Option 3: EC2 t2.micro (Free for 12 months)

If you want a persistent server:

### Setup

1. **Launch EC2 instance**:

   - Go to EC2 → Launch Instance
   - Choose: Ubuntu 22.04 LTS
   - Instance type: t2.micro (free tier)
   - Create key pair and download .pem file

1. **Connect to instance**:

   ```bash
   ssh -i your-key.pem ubuntu@your-ec2-ip
   ```

1. **Install dependencies**:

   ```bash
   sudo apt update
   sudo apt install python3-pip git
   git clone https://github.com/yourusername/rehoboam.git
   cd rehoboam
   pip3 install -r requirements.txt
   ```

1. **Set up environment**:

   ```bash
   cp .env.example .env
   nano .env  # Add your credentials
   ```

1. **Create cron job**:

   ```bash
   crontab -e

   # Add these lines:
   # Morning: 10:30 AM CET
   30 8 * * * cd /home/ubuntu/rehoboam && /usr/bin/python3 -m rehoboam.cli auto >> /home/ubuntu/logs/bot.log 2>&1

   # Evening: 6:00 PM CET
   0 16 * * * cd /home/ubuntu/rehoboam && /usr/bin/python3 -m rehoboam.cli auto >> /home/ubuntu/logs/bot.log 2>&1
   ```

1. **Create logs directory**:

   ```bash
   mkdir -p /home/ubuntu/logs
   ```

**Pros:**

- ✅ Full control
- ✅ SQLite works perfectly
- ✅ Easy to debug

**Cons:**

- ❌ Only free for 12 months
- ❌ Need to manage the server
- ❌ Must keep instance running 24/7

______________________________________________________________________

## Recommended Approach

**For now (learning week):**
Use **GitHub Actions** - easiest to set up, free, good for testing.

**For long-term (after learning week):**
Use **AWS Lambda** - free forever, fully automated, professional.

______________________________________________________________________

## Monitoring

### View Lambda Logs

```bash
aws logs tail /aws/lambda/rehoboam-trading-bot --follow
```

### View GitHub Actions Logs

- Go to your repo → Actions tab
- Click on latest run

### Set up Alerts (optional)

- AWS CloudWatch alarms → SNS → Email
- GitHub Actions will email you on failures

______________________________________________________________________

## Security Notes

1. **Never commit credentials** to git
1. **Use environment variables** or AWS Secrets Manager
1. **Enable 2FA** on your Kickbase account if possible
1. **Review trades** regularly to ensure bot is behaving correctly

______________________________________________________________________

## Troubleshooting

**Lambda timeout error:**

- Increase timeout in deploy script (max 15 min)
- Reduce max_trades_per_session

**GitHub Actions runs out of minutes:**

- Check you're not running too frequently
- Use AWS Lambda instead

**Bot not running:**

- Check timezone settings (CET vs UTC)
- Verify cron expressions
- Check logs for errors
