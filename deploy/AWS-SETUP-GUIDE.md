# AWS Setup Guide for Rehoboam Bot

## Step 1: Create IAM User (for deployment)

1. **Log in to AWS Console** as root user

1. **Go to IAM**:

   - Search for "IAM" in the top search bar
   - Click "IAM" (Identity and Access Management)

1. **Create User**:

   - Click "Users" in the left sidebar
   - Click "Create user"
   - Username: `rehoboam-deployer`
   - Click "Next"

1. **Set Permissions**:

   - Select "Attach policies directly"
   - Search and select these policies:
     - ✅ `AWSLambda_FullAccess`
     - ✅ `IAMFullAccess` (needed to create the Lambda execution role)
     - ✅ `CloudWatchLogsFullAccess`
     - ✅ `AmazonEventBridgeFullAccess`
     - ✅ `AmazonS3FullAccess` (for storing the learning database)
   - Click "Next"
   - Click "Create user"

1. **Create Access Keys**:

   - Click on the user you just created (`rehoboam-deployer`)
   - Click "Security credentials" tab
   - Scroll down to "Access keys"
   - Click "Create access key"
   - Select "Command Line Interface (CLI)"
   - Check "I understand the above recommendation"
   - Click "Next"
   - Description: "Rehoboam deployment"
   - Click "Create access key"
   - **⚠️ IMPORTANT**: Copy both:
     - Access key ID
     - Secret access key
   - Click "Done"

1. **Configure AWS CLI**:

   ```bash
   aws configure
   # Enter the access key ID you just copied
   # Enter the secret access key you just copied
   # Region: eu-central-1 (Frankfurt - closest to Germany)
   # Output format: json
   ```

1. **Test configuration**:

   ```bash
   aws sts get-caller-identity
   # Should show your user ARN
   ```

______________________________________________________________________

## Step 2: Create Lambda Execution Role

This is the role that Lambda will use when running your bot.

1. **Go to IAM → Roles**:

   - Click "Roles" in the left sidebar
   - Click "Create role"

1. **Select trusted entity**:

   - Select "AWS service"
   - Use case: Select "Lambda"
   - Click "Next"

1. **Add permissions**:

   - Search and select: `AWSLambdaBasicExecutionRole`
   - Search and select: `AmazonS3FullAccess` (for learning database)
   - Click "Next"

1. **Name the role**:

   - Role name: `rehoboam-lambda-execution-role`
   - Description: "Execution role for Rehoboam trading bot"
   - Click "Create role"

1. **Copy the Role ARN**:

   - Click on the role you just created
   - Copy the "ARN" (looks like: `arn:aws:iam::123456789012:role/rehoboam-lambda-execution-role`)
   - **Save this ARN** - you'll need it in Step 3

______________________________________________________________________

## Step 3: Get Your AWS Account ID

```bash
aws sts get-caller-identity --query Account --output text
```

Save this number (e.g., `123456789012`) - you'll need it next.

______________________________________________________________________

## Step 4: Create S3 Bucket (for learning database)

```bash
# Replace YOUR_UNIQUE_NAME with something unique (e.g., rehoboam-marco-2025)
aws s3 mb s3://rehoboam-YOUR_UNIQUE_NAME --region eu-central-1
```

**Save the bucket name** - you'll need it for deployment.

______________________________________________________________________

## Step 5: Update Deployment Script

1. **Edit the deployment script**:

   ```bash
   nano deploy/deploy-lambda.sh
   ```

1. **Update these values** (around line 10-15):

   ```bash
   ACCOUNT_ID="123456789012"  # Your account ID from Step 3
   ROLE_ARN="arn:aws:iam::123456789012:role/rehoboam-lambda-execution-role"  # From Step 2
   S3_BUCKET="rehoboam-YOUR_UNIQUE_NAME"  # From Step 4
   ```

1. **Update environment variables** (around line 50):

   ```bash
   KICKBASE_EMAIL=your-email@example.com
   KICKBASE_PASSWORD=your-password
   ```

1. **Save and exit** (Ctrl+X, Y, Enter)

______________________________________________________________________

## Step 6: Deploy!

```bash
chmod +x deploy/deploy-lambda.sh
./deploy/deploy-lambda.sh
```

______________________________________________________________________

## Step 7: Verify Deployment

1. **Check Lambda function exists**:

   ```bash
   aws lambda get-function --function-name rehoboam-trading-bot --region eu-central-1
   ```

1. **Test the function manually**:

   ```bash
   aws lambda invoke \
     --function-name rehoboam-trading-bot \
     --region eu-central-1 \
     output.json

   cat output.json
   ```

1. **View logs**:

   ```bash
   aws logs tail /aws/lambda/rehoboam-trading-bot --follow --region eu-central-1
   ```

1. **Check EventBridge schedules**:

   ```bash
   aws events list-rules --region eu-central-1 | grep rehoboam
   ```

______________________________________________________________________

## Step 8: Monitor Your Bot

### View Recent Logs

```bash
aws logs tail /aws/lambda/rehoboam-trading-bot --since 1h --region eu-central-1
```

### Check Next Scheduled Run

```bash
aws events describe-rule --name rehoboam-morning --region eu-central-1
aws events describe-rule --name rehoboam-evening --region eu-central-1
```

### Manually Trigger a Run

```bash
aws lambda invoke \
  --function-name rehoboam-trading-bot \
  --region eu-central-1 \
  --cli-binary-format raw-in-base64-out \
  output.json && cat output.json
```

______________________________________________________________________

## Troubleshooting

### Error: "User is not authorized"

- Check your IAM user has the correct policies attached
- Re-run `aws configure` with the correct access keys

### Error: "Role does not exist"

- Make sure you created the Lambda execution role in Step 2
- Check the ARN is correct in the deployment script

### Error: "Bucket does not exist"

- Create the S3 bucket in Step 4
- Check the bucket name is correct in the deployment script

### Lambda timeout

- Increase timeout in deploy script (max 900 seconds / 15 minutes)
- Consider reducing max_trades_per_session

### Bot not running on schedule

- Check EventBridge rules are enabled
- Verify timezone (cron uses UTC, not CET)
- Check Lambda has permission to be invoked by EventBridge

______________________________________________________________________

## Security Best Practices

1. **Never share your access keys**
1. **Enable MFA** on your root account
1. **Review CloudWatch logs** regularly
1. **Set up billing alerts** (AWS Console → Billing → Billing Preferences)
1. **Rotate access keys** every 90 days

______________________________________________________________________

## Costs

Within AWS Free Tier:

- Lambda: 1M requests/month FREE (you'll use ~60/month)
- S3: First 5GB FREE (database is \<1MB)
- CloudWatch: 5GB logs FREE
- EventBridge: FREE

**Expected monthly cost: $0.00** ✅

After 12 months (Free Tier expires):

- Lambda: $0.20/million requests (~$0.001/month for 60 requests)
- S3: $0.023/GB (~$0.00002/month for 1MB)
- **Total: \< $0.01/month** 🎉

______________________________________________________________________

## Next Steps

After successful deployment:

1. **Monitor for 1 week** to ensure it's working
1. **Check logs daily** to verify trades
1. **Review bid learning statistics**:
   ```bash
   # Connect to Lambda and download database
   # Then run locally:
   python -m rehoboam.cli stats
   ```
1. **Adjust settings** if needed based on performance

______________________________________________________________________

## Rolling Back / Uninstalling

To remove everything:

```bash
# Delete Lambda function
aws lambda delete-function --function-name rehoboam-trading-bot --region eu-central-1

# Delete EventBridge rules
aws events remove-targets --rule rehoboam-morning --ids 1 --region eu-central-1
aws events delete-rule --name rehoboam-morning --region eu-central-1

aws events remove-targets --rule rehoboam-evening --ids 1 --region eu-central-1
aws events delete-rule --name rehoboam-evening --region eu-central-1

# Delete S3 bucket (careful - this deletes your learning data!)
aws s3 rb s3://rehoboam-YOUR_UNIQUE_NAME --force --region eu-central-1
```
