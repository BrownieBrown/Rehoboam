#!/bin/bash
# Bicep-based Azure deployment for Rehoboam (REH-48).
#
# Usage:
#   bash deploy/deploy.sh                  # provision + publish both function apps (default)
#   bash deploy/deploy.sh infra            # just Bicep deploy (no code publish)
#   bash deploy/deploy.sh infra --what-if  # preview Bicep changes, no apply
#   bash deploy/deploy.sh code             # just func publish both function apps
#   bash deploy/deploy.sh code trading     # publish trading function only
#   bash deploy/deploy.sh code external    # publish external function only
#
# WARNING: 'bash deploy/deploy.sh infra' alone wipes WEBSITE_RUN_FROM_PACKAGE
# from app settings. Always follow with 'code' to restore the package
# reference, OR use the default 'all' which does both.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RESOURCE_GROUP="rg-rehoboam"
LOCATION="germanywestcentral"
BICEP_TEMPLATE="$SCRIPT_DIR/bicep/main.bicep"
BICEP_PARAMS="$SCRIPT_DIR/bicep/main.bicepparam"

ACTION="${1:-all}"
SUBACTION="${2:-}"

source_env() {
  local env_file="$PROJECT_ROOT/.env"
  if [[ ! -f "$env_file" ]]; then
    env_file="$HOME/.rehoboam.env"
  fi
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  else
    echo "ERROR: no .env file found at $PROJECT_ROOT/.env or $HOME/.rehoboam.env" >&2
    exit 1
  fi
}

# Optional notification channels. Absent means the channel stays disabled —
# the bot keeps trading, it just cannot tell anyone about it. Passed through
# to Bicep, which only creates a Key Vault secret when the value is non-empty.
notify_params() {
  echo "telegramBotToken=${TELEGRAM_BOT_TOKEN:-}" \
       "telegramChatId=${TELEGRAM_CHAT_ID:-}" \
       "telegramWebhookSecret=${TELEGRAM_WEBHOOK_SECRET:-}" \
       "smtpHost=${SMTP_HOST:-}" \
       "smtpPort=${SMTP_PORT:-587}" \
       "smtpUser=${SMTP_USER:-}" \
       "smtpPassword=${SMTP_PASSWORD:-}" \
       "alertEmailTo=${ALERT_EMAIL_TO:-}"
}

deploy_infra() {
  source_env
  : "${KICKBASE_EMAIL:?must be set in .env}"
  : "${KICKBASE_PASSWORD:?must be set in .env}"

  if [[ "$SUBACTION" == "--what-if" ]]; then
    echo "==> Running Bicep what-if (preview only)..."
    az deployment group what-if \
      --resource-group "$RESOURCE_GROUP" \
      --template-file "$BICEP_TEMPLATE" \
      --parameters "$BICEP_PARAMS" \
      --parameters kickbaseEmail="$KICKBASE_EMAIL" kickbasePassword="$KICKBASE_PASSWORD" \
      --parameters $(notify_params)
    return
  fi

  echo "==> Ensuring resource group $RESOURCE_GROUP exists..."
  az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

  echo "==> Deploying Bicep template to $RESOURCE_GROUP..."
  az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$BICEP_TEMPLATE" \
    --parameters "$BICEP_PARAMS" \
    --parameters kickbaseEmail="$KICKBASE_EMAIL" kickbasePassword="$KICKBASE_PASSWORD" \
    --parameters $(notify_params) \
    --output table
}

publish_function() {
  local app_name="$1"
  local src_dir="$2"

  if [[ ! -d "$src_dir" ]]; then
    echo "==> Skipping $app_name: source dir $src_dir does not exist yet."
    return
  fi

  echo "==> Publishing $app_name from $src_dir..."

  local deploy_dir
  deploy_dir="$(mktemp -d)"
  trap 'rm -rf "$deploy_dir"' EXIT

  cp "$src_dir"/{function_app.py,host.json,requirements.txt} "$deploy_dir/"
  cp -r "$PROJECT_ROOT/rehoboam" "$deploy_dir/"
  cp "$PROJECT_ROOT/pyproject.toml" "$PROJECT_ROOT/README.md" "$deploy_dir/"

  (cd "$deploy_dir" && func azure functionapp publish "$app_name" --python)

  rm -rf "$deploy_dir"
  trap - EXIT
}

deploy_code() {
  local target="${SUBACTION:-both}"
  if [[ "$target" == "both" || "$target" == "trading" ]]; then
    publish_function "func-rehoboam" "$SCRIPT_DIR/azure_function"
  fi
  if [[ "$target" == "both" || "$target" == "external" ]]; then
    publish_function "func-rehoboam-external" "$SCRIPT_DIR/azure_function_external_refresh"
  fi
}

case "$ACTION" in
  all)
    deploy_infra
    deploy_code
    ;;
  infra)
    deploy_infra
    ;;
  code)
    deploy_code
    ;;
  *)
    echo "Usage: $0 [all|infra [--what-if]|code [trading|external]]" >&2
    exit 1
    ;;
esac

echo "==> Done."
