#!/bin/bash
# Deploy rehoboam to AWS Lambda - Updated version with S3 support

set -e

echo "🚀 Deploying Rehoboam to AWS Lambda..."

# ============================================================
# CONFIGURATION - UPDATE THESE VALUES!
# ============================================================

# Your AWS Account ID (get it with: aws sts get-caller-identity --query Account --output text)
ACCOUNT_ID="REPLACE_WITH_YOUR_ACCOUNT_ID"

# Lambda execution role ARN (from Step 2 of setup guide)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/rehoboam-lambda-execution-role"

# S3 bucket for learning database (created in Step 4)
S3_BUCKET="rehoboam-REPLACE_WITH_YOUR_BUCKET_NAME"

# Your Kickbase credentials
KICKBASE_EMAIL="your-email@example.com"
KICKBASE_PASSWORD="your-password"

# League index (usually 0 for first league)
LEAGUE_INDEX="0"

# Dry run mode (set to false for real trades)
DRY_RUN="false"

# Lambda configuration
FUNCTION_NAME="rehoboam-trading-bot"
REGION="eu-central-1"  # Frankfurt (close to Germany)
MEMORY_SIZE=1024  # Increased for better performance
TIMEOUT=900  # 15 minutes (max for Lambda)

# ============================================================
# VALIDATION
# ============================================================

if [[ "$ACCOUNT_ID" == "REPLACE_WITH_YOUR_ACCOUNT_ID" ]]; then
    echo "❌ Error: Please update ACCOUNT_ID in this script"
    echo "   Run: aws sts get-caller-identity --query Account --output text"
    exit 1
fi

if [[ "$S3_BUCKET" == "rehoboam-REPLACE_WITH_YOUR_BUCKET_NAME" ]]; then
    echo "❌ Error: Please update S3_BUCKET in this script"
    echo "   Example: rehoboam-marco-2025"
    exit 1
fi

if [[ "$KICKBASE_EMAIL" == "your-email@example.com" ]]; then
    echo "❌ Error: Please update KICKBASE_EMAIL in this script"
    exit 1
fi

echo "✅ Configuration validated"

# ============================================================
# CREATE DEPLOYMENT PACKAGE
# ============================================================

echo "📦 Creating deployment package..."
rm -rf deploy/package
mkdir -p deploy/package

# Install dependencies
echo "   Installing Python dependencies..."
pip install -r deploy/requirements-lambda.txt -t deploy/package/ --quiet

# Copy rehoboam code
echo "   Copying source code..."
cp -r rehoboam deploy/package/
cp deploy/lambda_handler.py deploy/package/

# Create zip
echo "   Creating ZIP archive..."
cd deploy/package
zip -r ../lambda-deployment.zip . -x "*.pyc" -x "*__pycache__*" -x "*.git*" > /dev/null
cd ../..

FILE_SIZE=$(du -h deploy/lambda-deployment.zip | cut -f1)
echo "✅ Package created: deploy/lambda-deployment.zip ($FILE_SIZE)"

# ============================================================
# CHECK S3 BUCKET
# ============================================================

echo "☁️  Checking S3 bucket..."
if aws s3 ls "s3://${S3_BUCKET}" --region $REGION 2>/dev/null; then
    echo "✅ S3 bucket exists: ${S3_BUCKET}"
else
    echo "📦 Creating S3 bucket: ${S3_BUCKET}"
    aws s3 mb "s3://${S3_BUCKET}" --region $REGION
    echo "✅ S3 bucket created"
fi

# ============================================================
# DEPLOY LAMBDA FUNCTION
# ============================================================

echo "☁️  Deploying to AWS Lambda..."

# Check if function exists
if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>/dev/null > /dev/null; then
    echo "   Updating existing function..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://deploy/lambda-deployment.zip \
        --region $REGION > /dev/null

    echo "   Updating configuration..."
    aws lambda update-function-configuration \
        --function-name $FUNCTION_NAME \
        --timeout $TIMEOUT \
        --memory-size $MEMORY_SIZE \
        --environment "Variables={
            KICKBASE_EMAIL=${KICKBASE_EMAIL},
            KICKBASE_PASSWORD=${KICKBASE_PASSWORD},
            LEAGUE_INDEX=${LEAGUE_INDEX},
            DRY_RUN=${DRY_RUN},
            S3_BUCKET=${S3_BUCKET}
        }" \
        --region $REGION > /dev/null

    echo "✅ Lambda function updated"
else
    echo "   Creating new function..."
    aws lambda create-function \
        --function-name $FUNCTION_NAME \
        --runtime python3.11 \
        --role $ROLE_ARN \
        --handler lambda_handler.lambda_handler \
        --zip-file fileb://deploy/lambda-deployment.zip \
        --timeout $TIMEOUT \
        --memory-size $MEMORY_SIZE \
        --region $REGION \
        --environment "Variables={
            KICKBASE_EMAIL=${KICKBASE_EMAIL},
            KICKBASE_PASSWORD=${KICKBASE_PASSWORD},
            LEAGUE_INDEX=${LEAGUE_INDEX},
            DRY_RUN=${DRY_RUN},
            S3_BUCKET=${S3_BUCKET}
        }" > /dev/null

    echo "✅ Lambda function created"
fi

# ============================================================
# SETUP EVENTBRIDGE SCHEDULES
# ============================================================

echo "⏰ Setting up EventBridge schedules..."

# Morning run: 10:30 AM CET = 08:30 UTC (winter) / 09:30 UTC (summer)
# Using 08:30 UTC for CET standard time
echo "   Creating morning schedule (10:30 AM CET)..."
aws events put-rule \
    --name rehoboam-morning \
    --schedule-expression "cron(30 8 * * ? *)" \
    --region $REGION \
    --description "Run Rehoboam at 10:30 AM CET (after market value update)" > /dev/null

# Evening run: 6:00 PM CET = 16:00 UTC (winter) / 17:00 UTC (summer)
echo "   Creating evening schedule (6:00 PM CET)..."
aws events put-rule \
    --name rehoboam-evening \
    --schedule-expression "cron(0 16 * * ? *)" \
    --region $REGION \
    --description "Run Rehoboam at 6:00 PM CET" > /dev/null

# Get Lambda ARN
LAMBDA_ARN=$(aws lambda get-function --function-name $FUNCTION_NAME --region $REGION --query 'Configuration.FunctionArn' --output text)

# Add Lambda permissions for EventBridge
echo "   Granting EventBridge permissions..."
aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id rehoboam-morning-permission \
    --action 'lambda:InvokeFunction' \
    --principal events.amazonaws.com \
    --source-arn $(aws events describe-rule --name rehoboam-morning --region $REGION --query 'Arn' --output text) \
    --region $REGION 2>/dev/null || true

aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id rehoboam-evening-permission \
    --action 'lambda:InvokeFunction' \
    --principal events.amazonaws.com \
    --source-arn $(aws events describe-rule --name rehoboam-evening --region $REGION --query 'Arn' --output text) \
    --region $REGION 2>/dev/null || true

# Add Lambda as target for rules
echo "   Connecting schedules to Lambda..."
aws events put-targets \
    --rule rehoboam-morning \
    --targets "Id"="1","Arn"="${LAMBDA_ARN}" \
    --region $REGION > /dev/null

aws events put-targets \
    --rule rehoboam-evening \
    --targets "Id"="1","Arn"="${LAMBDA_ARN}" \
    --region $REGION > /dev/null

echo "✅ Schedules configured"

# ============================================================
# CLEANUP
# ============================================================

echo "🧹 Cleaning up temporary files..."
rm -rf deploy/package
echo "✅ Cleanup complete"

# ============================================================
# SUCCESS!
# ============================================================

echo ""
echo "════════════════════════════════════════════════════════════"
echo "✅ Deployment Complete!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Your bot will now run automatically:"
echo "  🌅 Morning: 10:30 AM CET (after market value updates)"
echo "  🌆 Evening: 6:00 PM CET"
echo ""
echo "Next steps:"
echo "  1. Test manually:"
echo "     aws lambda invoke --function-name $FUNCTION_NAME --region $REGION output.json && cat output.json"
echo ""
echo "  2. View logs:"
echo "     aws logs tail /aws/lambda/$FUNCTION_NAME --follow --region $REGION"
echo ""
echo "  3. Check schedule status:"
echo "     aws events list-rules --region $REGION | grep rehoboam"
echo ""
echo "  4. Monitor S3 database:"
echo "     aws s3 ls s3://${S3_BUCKET}/"
echo ""
echo "════════════════════════════════════════════════════════════"
