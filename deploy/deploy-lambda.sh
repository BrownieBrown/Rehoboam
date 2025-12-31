#!/bin/bash
# Deploy rehoboam to AWS Lambda

set -e

echo "🚀 Deploying Rehoboam to AWS Lambda..."

# Configuration
FUNCTION_NAME="rehoboam-trading-bot"
REGION="eu-central-1"  # Frankfurt (close to Germany)
MEMORY_SIZE=512
TIMEOUT=900  # 15 minutes (max for Lambda)

# Create deployment package
echo "📦 Creating deployment package..."
rm -rf deploy/package
mkdir -p deploy/package

# Install dependencies
pip install -r deploy/requirements-lambda.txt -t deploy/package/

# Copy rehoboam code
cp -r rehoboam deploy/package/
cp deploy/lambda_handler.py deploy/package/

# Create zip
cd deploy/package
zip -r ../lambda-deployment.zip . -x "*.pyc" -x "*__pycache__*"
cd ../..

echo "✅ Package created: deploy/lambda-deployment.zip"

# Create/Update Lambda function
echo "☁️  Deploying to AWS Lambda..."

# Check if function exists
if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://deploy/lambda-deployment.zip \
        --region $REGION
else
    echo "Creating new function..."
    aws lambda create-function \
        --function-name $FUNCTION_NAME \
        --runtime python3.11 \
        --role arn:aws:iam::YOUR_ACCOUNT_ID:role/lambda-execution-role \
        --handler lambda_handler.lambda_handler \
        --zip-file fileb://deploy/lambda-deployment.zip \
        --timeout $TIMEOUT \
        --memory-size $MEMORY_SIZE \
        --region $REGION \
        --environment "Variables={
            KICKBASE_EMAIL=your-email@example.com,
            KICKBASE_PASSWORD=your-password,
            LEAGUE_INDEX=0,
            DRY_RUN=false
        }"
fi

echo "✅ Lambda function deployed!"

# Create EventBridge schedule
echo "⏰ Setting up schedule..."

# Morning run: 10:30 AM CET (after market value update at 10:00 AM)
aws events put-rule \
    --name rehoboam-morning \
    --schedule-expression "cron(30 8 * * ? *)" \
    --region $REGION \
    --description "Run Rehoboam at 10:30 AM CET"

# Evening run: 6:00 PM CET
aws events put-rule \
    --name rehoboam-evening \
    --schedule-expression "cron(0 16 * * ? *)" \
    --region $REGION \
    --description "Run Rehoboam at 6:00 PM CET"

# Add Lambda permissions
aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id rehoboam-morning \
    --action 'lambda:InvokeFunction' \
    --principal events.amazonaws.com \
    --source-arn $(aws events describe-rule --name rehoboam-morning --region $REGION --query 'Arn' --output text) \
    --region $REGION || true

aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id rehoboam-evening \
    --action 'lambda:InvokeFunction' \
    --principal events.amazonaws.com \
    --source-arn $(aws events describe-rule --name rehoboam-evening --region $REGION --query 'Arn' --output text) \
    --region $REGION || true

# Add targets
aws events put-targets \
    --rule rehoboam-morning \
    --targets "Id"="1","Arn"="$(aws lambda get-function --function-name $FUNCTION_NAME --region $REGION --query 'Configuration.FunctionArn' --output text)" \
    --region $REGION

aws events put-targets \
    --rule rehoboam-evening \
    --targets "Id"="1","Arn"="$(aws lambda get-function --function-name $FUNCTION_NAME --region $REGION --query 'Configuration.FunctionArn' --output text)" \
    --region $REGION

echo "✅ Deployment complete!"
echo ""
echo "Your bot will now run automatically:"
echo "  - Morning: 10:30 AM CET (after market value updates)"
echo "  - Evening: 6:00 PM CET"
echo ""
echo "View logs: aws logs tail /aws/lambda/$FUNCTION_NAME --follow --region $REGION"
