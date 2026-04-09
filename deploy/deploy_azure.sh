#!/bin/bash
# Deploy Rehoboam to Azure Functions (Consumption plan - free tier)
#
# Prerequisites:
#   - Azure CLI installed: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli
#   - Azure Functions Core Tools: npm install -g azure-functions-core-tools@4
#   - Logged in: az login
#
# Usage:
#   chmod +x deploy/deploy_azure.sh
#   ./deploy/deploy_azure.sh

set -euo pipefail

# Configuration
RESOURCE_GROUP="rg-rehoboam"
LOCATION="germanywestcentral"
STORAGE_ACCOUNT="strehoboam$(date +%s | tail -c 5)"  # Must be globally unique
FUNCTION_APP="func-rehoboam"
BLOB_CONTAINER="rehoboam-data"
PYTHON_VERSION="3.11"

echo "========================================="
echo "  Rehoboam Azure Functions Deployment"
echo "========================================="

# Check prerequisites
if ! command -v az &> /dev/null; then
    echo "ERROR: Azure CLI not installed. Run: brew install azure-cli"
    exit 1
fi

if ! command -v func &> /dev/null; then
    echo "ERROR: Azure Functions Core Tools not installed. Run: npm install -g azure-functions-core-tools@4"
    exit 1
fi

# Check login
if ! az account show &> /dev/null 2>&1; then
    echo "Not logged in to Azure. Running az login..."
    az login
fi

echo ""
echo "Step 1: Creating resource group..."
az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none
echo "  ✓ Resource group: $RESOURCE_GROUP ($LOCATION)"

echo ""
echo "Step 2: Creating storage account..."
# Check if storage account already exists
EXISTING_STORAGE=$(az storage account list --resource-group "$RESOURCE_GROUP" --query "[0].name" -o tsv 2>/dev/null || true)
if [ -n "$EXISTING_STORAGE" ]; then
    STORAGE_ACCOUNT="$EXISTING_STORAGE"
    echo "  ✓ Using existing storage account: $STORAGE_ACCOUNT"
else
    az storage account create \
        --name "$STORAGE_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --sku Standard_LRS \
        --kind StorageV2 \
        --output none
    echo "  ✓ Storage account: $STORAGE_ACCOUNT"
fi

# Get connection string
CONNECTION_STRING=$(az storage account show-connection-string \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --query connectionString -o tsv)

echo ""
echo "Step 3: Creating blob container for databases..."
az storage container create \
    --name "$BLOB_CONTAINER" \
    --connection-string "$CONNECTION_STRING" \
    --output none 2>/dev/null || true
echo "  ✓ Blob container: $BLOB_CONTAINER"

echo ""
echo "Step 4: Creating function app..."
# Check if function app already exists
if az functionapp show --name "$FUNCTION_APP" --resource-group "$RESOURCE_GROUP" &> /dev/null 2>&1; then
    echo "  ✓ Function app already exists: $FUNCTION_APP"
else
    az functionapp create \
        --name "$FUNCTION_APP" \
        --resource-group "$RESOURCE_GROUP" \
        --storage-account "$STORAGE_ACCOUNT" \
        --consumption-plan-location "$LOCATION" \
        --runtime python \
        --runtime-version "$PYTHON_VERSION" \
        --functions-version 4 \
        --os-type Linux \
        --output none
    echo "  ✓ Function app: $FUNCTION_APP"
fi

echo ""
echo "Step 5: Configuring app settings..."
echo "  Loading credentials from .env file..."

# Load .env file if it exists
ENV_FILE="$(dirname "$0")/../.env"
if [ ! -f "$ENV_FILE" ]; then
    ENV_FILE="$HOME/.rehoboam.env"
fi

if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a
    source "$ENV_FILE"
    set +a
    echo "  ✓ Loaded .env from $ENV_FILE"
else
    echo "  WARNING: No .env file found. Set credentials manually."
fi

az functionapp config appsettings set \
    --name "$FUNCTION_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --settings \
        "KICKBASE_EMAIL=${KICKBASE_EMAIL:-}" \
        "KICKBASE_PASSWORD=${KICKBASE_PASSWORD:-}" \
        "LEAGUE_INDEX=${LEAGUE_INDEX:-0}" \
        "DRY_RUN=${DRY_RUN:-true}" \
        "AZURE_STORAGE_CONNECTION_STRING=$CONNECTION_STRING" \
        "BLOB_CONTAINER=$BLOB_CONTAINER" \
    --output none
echo "  ✓ App settings configured"

echo ""
echo "Step 6: Preparing deployment package..."
DEPLOY_DIR=$(mktemp -d)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Copy Azure Function files
cp "$SCRIPT_DIR/azure_function/function_app.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/azure_function/host.json" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/azure_function/requirements.txt" "$DEPLOY_DIR/"

# Copy rehoboam package
cp -r "$PROJECT_ROOT/rehoboam" "$DEPLOY_DIR/"

# Copy pyproject.toml and README for install
cp "$PROJECT_ROOT/pyproject.toml" "$DEPLOY_DIR/"
cp "$PROJECT_ROOT/README.md" "$DEPLOY_DIR/"

echo "  ✓ Package prepared in $DEPLOY_DIR"

echo ""
echo "Step 7: Deploying to Azure..."
cd "$DEPLOY_DIR"
func azure functionapp publish "$FUNCTION_APP" --python
cd -

# Cleanup
rm -rf "$DEPLOY_DIR"

echo ""
echo "========================================="
echo "  Deployment Complete!"
echo "========================================="
echo ""
echo "Function App: $FUNCTION_APP"
echo "Resource Group: $RESOURCE_GROUP"
echo "Schedule: 2x daily at 08:00 and 20:00 UTC (10:00/22:00 Europe/Berlin summer)"
echo ""
echo "Next steps:"
echo "  1. Verify credentials: az functionapp config appsettings list -n $FUNCTION_APP -g $RESOURCE_GROUP"
echo "  2. Check logs: az functionapp log tail -n $FUNCTION_APP -g $RESOURCE_GROUP"
echo "  3. Test manually: az functionapp function invoke -n $FUNCTION_APP -g $RESOURCE_GROUP --function-name trading_session"
echo "  4. When ready, set DRY_RUN=false:"
echo "     az functionapp config appsettings set -n $FUNCTION_APP -g $RESOURCE_GROUP --settings DRY_RUN=false"
echo ""
echo "Monthly cost: \$0 (Consumption plan free tier)"
