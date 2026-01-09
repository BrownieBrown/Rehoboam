#!/bin/bash
# Deploy Rehoboam trading bot to AWS Lambda

set -e  # Exit on error

echo "=========================================="
echo "Rehoboam AWS Lambda Deployment Script"
echo "=========================================="

# Configuration
FUNCTION_NAME="rehoboam-trading-bot"
RUNTIME="python3.11"
HANDLER="lambda_handler.lambda_handler"
MEMORY_SIZE=512  # MB (free tier: up to 3008 MB with sufficient execution time)
TIMEOUT=300      # seconds (5 minutes, max for automation)
REGION="us-east-1"  # Change to your preferred region

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "ERROR: AWS CLI not found. Please install it first:"
    echo "  brew install awscli  # macOS"
    echo "  pip install awscli   # Python"
    exit 1
fi

# Check if logged in to AWS
if ! aws sts get-caller-identity &> /dev/null; then
    echo "ERROR: Not authenticated with AWS. Run 'aws configure' first."
    exit 1
fi

echo "Building Lambda deployment package..."

# Create build directory
BUILD_DIR="lambda_build"
rm -rf $BUILD_DIR
mkdir -p $BUILD_DIR

# Copy source code
echo "Copying source files..."
cp -r rehoboam $BUILD_DIR/
cp lambda_handler.py $BUILD_DIR/

# Install dependencies into build directory
echo "Installing Python dependencies..."
if [ ! -f "requirements.txt" ]; then
    echo "ERROR: requirements.txt not found!"
    echo "Please create requirements.txt or run: pip freeze > requirements.txt"
    exit 1
fi

pip install -r requirements.txt -t $BUILD_DIR/ --quiet --upgrade

# Remove unnecessary files to reduce package size
echo "Cleaning up unnecessary files..."
cd $BUILD_DIR
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete
find . -type f -name "*.pyo" -delete
rm -rf boto3* botocore*  # Already available in Lambda runtime

# Create ZIP package
echo "Creating deployment package..."
zip -r ../rehoboam-lambda.zip . -q

cd ..

echo "Package created: rehoboam-lambda.zip ($(du -h rehoboam-lambda.zip | cut -f1))"

# Check if Lambda function exists
if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION &> /dev/null; then
    echo "Updating existing Lambda function..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://rehoboam-lambda.zip \
        --region $REGION \
        --no-cli-pager

    echo "Waiting for update to complete..."
    aws lambda wait function-updated --function-name $FUNCTION_NAME --region $REGION

    echo "Lambda function updated successfully!"
else
    echo "Lambda function not found. Please create it first using the AWS Console or:"
    echo ""
    echo "  aws lambda create-function \\"
    echo "    --function-name $FUNCTION_NAME \\"
    echo "    --runtime $RUNTIME \\"
    echo "    --handler $HANDLER \\"
    echo "    --memory-size $MEMORY_SIZE \\"
    echo "    --timeout $TIMEOUT \\"
    echo "    --role arn:aws:iam::YOUR_ACCOUNT_ID:role/lambda-execution-role \\"
    echo "    --zip-file fileb://rehoboam-lambda.zip \\"
    echo "    --region $REGION"
    echo ""
    echo "See DEPLOYMENT.md for detailed setup instructions."
fi

# Cleanup
echo "Cleaning up build directory..."
rm -rf $BUILD_DIR

echo ""
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Set environment variables in AWS Lambda console:"
echo "   - KICKBASE_EMAIL"
echo "   - KICKBASE_PASSWORD"
echo "   - DRY_RUN=true (set to 'false' for real trading)"
echo ""
echo "2. Test the function:"
echo "   aws lambda invoke --function-name $FUNCTION_NAME output.json --region $REGION"
echo ""
echo "3. Set up EventBridge schedule (see DEPLOYMENT.md)"
echo ""
