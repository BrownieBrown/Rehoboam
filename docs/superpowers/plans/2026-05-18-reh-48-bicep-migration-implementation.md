# REH-48 Bicep Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the imperative `deploy/deploy_azure.sh` bash provisioning with declarative Bicep templates. Move all sensitive secrets to Azure Key Vault, accessed via system-assigned managed identities. Model both function apps (existing trading + new `func-rehoboam-external` for REH-41 P2) so REH-41 P2 Task 2.14 just adds the second function app's code.

**Architecture:** Modular Bicep (`main.bicep` orchestrating shared infra + `modules/function-app.bicep` called twice for the two function apps). 4 KV secrets accessed by function apps via system-assigned identities + Key Vault Secrets User role assignments. App settings live in separate `Microsoft.Web/sites/config` sub-resources so they `dependsOn` the role assignments — without this, KV references can fail to resolve at first function startup. A new `deploy/deploy.sh` handles both `infra` and `code` actions with positional subcommands.

**Tech Stack:** Bicep (Azure ARM IaC), Azure Resource Manager API versions 2023-05-01 (storage), 2023-01-01 (web/sites + serverfarms), 2023-07-01 (keyvault), 2022-04-01 (role assignments), 2020-02-02 (insights). `az` CLI for deployment. Bash for the wrapper script. No Python code changes.

**Reference spec:** `docs/superpowers/specs/2026-05-18-bicep-migration-design.md`

**Follow-up tickets**: REH-49 (KV public-network lockdown) and REH-50 (managed identity for blob, eliminating storage connection string) — both filed, both depend on this plan landing first.

______________________________________________________________________

## File Structure

### Phase 1 — Bicep template (no Azure changes)

- Create: `deploy/bicep/main.bicep` — root: parameters + shared infra (storage, plan, AI, KV) + module calls + role assignments + appsettings sub-resources + outputs
- Create: `deploy/bicep/modules/function-app.bicep` — reusable: one function-app site with system-assigned identity
- Create: `deploy/bicep/main.bicepparam` — non-secret parameter defaults

### Phase 2 — Deploy script + cleanup

- Create: `deploy/deploy.sh` — replaces `deploy/deploy_azure.sh` with Bicep-aware all/infra/code positional commands
- Delete: `deploy/deploy_azure.sh`
- Modify: `CLAUDE.md` — update "Common Commands" deploy section

### Phase 3 — Validation (read-only Azure access)

No files modified. Runs `az bicep build` (local) and `az deployment group what-if` (read-only Azure).

### Phase 4 — Production cutover (user-gated)

No files modified. Runs `bash deploy/deploy.sh infra`, `bash deploy/deploy.sh code trading`, then opens PR with smoke evidence.

______________________________________________________________________

# Phase 1 — Build the Bicep template

## Task 1: Bootstrap `main.bicep` with shared infra

**Files:**

- Create: `deploy/bicep/main.bicep`

- [ ] **Step 1: Create the bicep directory and skeleton file**

Run: `mkdir -p deploy/bicep/modules`

Create `deploy/bicep/main.bicep`:

```bicep
targetScope = 'resourceGroup'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Globally-unique storage account name. EXISTING prod: strehoboam6490.')
param storageAccountName string

@description('App Service Plan name. EXISTING prod (auto-generated): GermanyWestCentralLinuxDynamicPlan.')
param appServicePlanName string = 'GermanyWestCentralLinuxDynamicPlan'

@description('Application Insights name. EXISTING prod: func-rehoboam.')
param appInsightsName string = 'func-rehoboam'

@description('Blob container name for the bot state SQLite DBs + external/ JSON cache.')
param blobContainerName string = 'rehoboam-data'

// ---------------------------------------------------------------------------
// Shared infrastructure
// ---------------------------------------------------------------------------

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: blobContainerName
  properties: { publicAccess: 'None' }
}

resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: appServicePlanName
  location: location
  kind: 'linux'
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: { reserved: true }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    Request_Source: 'rest'
  }
}

output storageAccountName string = storageAccount.name
```

- [ ] **Step 2: Verify Bicep syntax**

Run: `az bicep build --file deploy/bicep/main.bicep`
Expected: Zero output (success). Produces `deploy/bicep/main.json` as a side effect — that's fine, it's gitignored by default but if not we'll add it in Task 6.

If errors: read the error message, fix syntax in `main.bicep`, retry. Common issues: missing `@2023-05-01` API version, wrong indentation, missing braces.

- [ ] **Step 3: Add `.bicep` build artifacts to .gitignore**

Check if `.gitignore` already excludes `*.json` next to `*.bicep`:

Run: `grep -n "bicep\|\\.json" .gitignore 2>/dev/null | head -5`

If `deploy/bicep/*.json` or similar is NOT excluded, add a line to `.gitignore`:

```
deploy/bicep/**/*.json
```

Use the Edit tool to append this line, NOT to rewrite the whole file.

- [ ] **Step 4: Commit**

```bash
git add deploy/bicep/main.bicep .gitignore
git commit -m "$(cat <<'EOF'
feat(deploy): bootstrap Bicep main.bicep with shared infra (REH-48)

Adds storage account, blob container, Y1 Linux app service plan, and
Application Insights to the Bicep template. Adopts existing prod
resources by name; tightens storage account to TLS 1.2 and disables
public blob access (no-op if already set).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If pre-commit reformats anything, accept the changes (`git add`) and retry.

______________________________________________________________________

## Task 2: Add `modules/function-app.bicep` module + 2x calls

**Files:**

- Create: `deploy/bicep/modules/function-app.bicep`

- Modify: `deploy/bicep/main.bicep`

- [ ] **Step 1: Create the module file**

Create `deploy/bicep/modules/function-app.bicep`:

```bicep
@description('Function app name.')
param name string

@description('Region.')
param location string

@description('Resource ID of the App Service Plan to host this function app.')
param appServicePlanId string

resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: name
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: appServicePlanId
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'FtpsOnly'
      minTlsVersion: '1.2'
      alwaysOn: false
    }
  }
}

output name string = functionApp.name
output principalId string = functionApp.identity.principalId
output functionAppId string = functionApp.id
```

- [ ] **Step 2: Add parameters + module calls to main.bicep**

In `deploy/bicep/main.bicep`, insert the following AFTER the existing `param blobContainerName string = 'rehoboam-data'` line and BEFORE the `// Shared infrastructure` section:

```bicep
@description('Trading-session function app name.')
param tradingFunctionAppName string = 'func-rehoboam'

@description('External-data refresh function app name (REH-41 Phase 2).')
param externalFunctionAppName string = 'func-rehoboam-external'
```

Then, AT THE END of `main.bicep` (after the existing `output storageAccountName ...` line), append:

```bicep

// ---------------------------------------------------------------------------
// Function apps (via reusable module)
// ---------------------------------------------------------------------------

module tradingFunction 'modules/function-app.bicep' = {
  name: 'tradingFunctionDeployment'
  params: {
    name: tradingFunctionAppName
    location: location
    appServicePlanId: appServicePlan.id
  }
}

module externalFunction 'modules/function-app.bicep' = {
  name: 'externalFunctionDeployment'
  params: {
    name: externalFunctionAppName
    location: location
    appServicePlanId: appServicePlan.id
  }
}

output tradingFunctionName string = tradingFunction.outputs.name
output externalFunctionName string = externalFunction.outputs.name
```

- [ ] **Step 3: Verify Bicep syntax**

Run: `az bicep build --file deploy/bicep/main.bicep`
Expected: zero errors. May print a warning about secure-handling for `name` parameter — ignore.

- [ ] **Step 4: Commit**

```bash
git add deploy/bicep/modules/function-app.bicep deploy/bicep/main.bicep
git commit -m "$(cat <<'EOF'
feat(deploy): function-app Bicep module + 2x calls (REH-48)

Reusable module creates a Linux Y1 function app with system-assigned
managed identity. Called twice from main.bicep — once for trading,
once for the future external-refresh function (REH-41 P2 Task 2.14).
Module returns name + principalId + functionAppId for downstream
role assignments.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 3: Add Key Vault + 4 secrets

**Files:**

- Modify: `deploy/bicep/main.bicep`

- [ ] **Step 1: Add KV parameter + KV resource + 4 secrets**

In `deploy/bicep/main.bicep`, insert the following AFTER the `param externalFunctionAppName ...` line and BEFORE the `// Shared infrastructure` section:

```bicep
@description('Key Vault name. Must be globally unique (3-24 chars, alphanumeric + hyphens).')
param keyVaultName string = 'kv-rehoboam-${uniqueString(resourceGroup().id)}'

@description('Kickbase login email.')
@secure()
param kickbaseEmail string

@description('Kickbase login password.')
@secure()
param kickbasePassword string
```

Then, AT THE END of `main.bicep` (after the trading/external module declarations + outputs), append:

```bicep

// ---------------------------------------------------------------------------
// Key Vault + secrets
// ---------------------------------------------------------------------------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enabledForDeployment: false
    publicNetworkAccess: 'Enabled'
    networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }
  }
}

resource secretKickbaseEmail 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'kickbase-email'
  properties: { value: kickbaseEmail }
}

resource secretKickbasePassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'kickbase-password'
  properties: { value: kickbasePassword }
}

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'

resource secretStorageConn 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'storage-connection-string'
  properties: { value: storageConnectionString }
}

resource secretAppInsightsConn 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'app-insights-connection-string'
  properties: { value: appInsights.properties.ConnectionString }
}

output keyVaultName string = keyVault.name
```

- [ ] **Step 2: Verify Bicep syntax**

Run: `az bicep build --file deploy/bicep/main.bicep`
Expected: zero errors. Bicep may print a warning about `listKeys()` being used inline; that's fine — the result is auto-secure-flagged.

- [ ] **Step 3: Commit**

```bash
git add deploy/bicep/main.bicep
git commit -m "$(cat <<'EOF'
feat(deploy): Key Vault + 4 secrets in Bicep (REH-48)

Adds Azure Key Vault with RBAC authorization, plus 4 secrets:
kickbase-email, kickbase-password, storage-connection-string,
app-insights-connection-string. Secrets sourced from @secure()
parameters (Kickbase credentials) and derived from other resources
(storage listKeys + AI connection string). Function apps will get
read access via role assignments in the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 4: Add role assignments (Key Vault Secrets User per function)

**Files:**

- Modify: `deploy/bicep/main.bicep`

- [ ] **Step 1: Add role assignments**

In `deploy/bicep/main.bicep`, AT THE END of the file (after the `output keyVaultName ...` line), append:

```bicep

// ---------------------------------------------------------------------------
// Role assignments — Key Vault Secrets User for each function's managed identity
// ---------------------------------------------------------------------------

// Built-in role: "Key Vault Secrets User"
// https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles#key-vault-secrets-user
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource tradingKvAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, tradingFunction.outputs.principalId, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: tradingFunction.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

resource externalKvAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, externalFunction.outputs.principalId, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: externalFunction.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}
```

- [ ] **Step 2: Verify Bicep syntax**

Run: `az bicep build --file deploy/bicep/main.bicep`
Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add deploy/bicep/main.bicep
git commit -m "$(cat <<'EOF'
feat(deploy): role assignments for Key Vault Secrets User (REH-48)

Each function app's system-assigned identity gets the Key Vault
Secrets User built-in role (role ID 4633458b-17de-408a-b874-0445c86b69e6)
scoped to our KV. RBAC name uses guid(kv, principal, role) for
deterministic + idempotent assignments.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 5: Add appsettings sub-resources (KV-referenced)

**Files:**

- Modify: `deploy/bicep/main.bicep`

- [ ] **Step 1: Add app behavior parameters + appsettings sub-resources**

In `deploy/bicep/main.bicep`, insert the following AFTER the `param kickbasePassword ...` block and BEFORE the `// Shared infrastructure` section:

```bicep
@description('League index in the Kickbase leagues list.')
param leagueIndex string = '0'

@description('When true, trading function simulates trades without placing them.')
param dryRun string = 'true'

@description('When true, trading function uses aggressive thresholds.')
param aggressiveMode string = 'true'
```

Then, AT THE END of the file (after the role assignment resources), append:

```bicep

// ---------------------------------------------------------------------------
// App settings — separate Microsoft.Web/sites/config sub-resources so they
// can dependsOn the role assignment. KV references resolve at runtime only
// after the function's managed identity has Get permission on the secrets.
// ---------------------------------------------------------------------------

resource tradingAppSettings 'Microsoft.Web/sites/config@2023-01-01' = {
  name: '${tradingFunctionAppName}/appsettings'
  dependsOn: [
    tradingFunction
    tradingKvAccess
  ]
  properties: {
    AzureWebJobsStorage: '@Microsoft.KeyVault(SecretUri=${secretStorageConn.properties.secretUri})'
    AZURE_STORAGE_CONNECTION_STRING: '@Microsoft.KeyVault(SecretUri=${secretStorageConn.properties.secretUri})'
    APPLICATIONINSIGHTS_CONNECTION_STRING: '@Microsoft.KeyVault(SecretUri=${secretAppInsightsConn.properties.secretUri})'
    KICKBASE_EMAIL: '@Microsoft.KeyVault(SecretUri=${secretKickbaseEmail.properties.secretUri})'
    KICKBASE_PASSWORD: '@Microsoft.KeyVault(SecretUri=${secretKickbasePassword.properties.secretUri})'
    FUNCTIONS_EXTENSION_VERSION: '~4'
    FUNCTIONS_WORKER_RUNTIME: 'python'
    LEAGUE_INDEX: leagueIndex
    DRY_RUN: dryRun
    AGGRESSIVE: aggressiveMode
    BLOB_CONTAINER: blobContainerName
  }
}

resource externalAppSettings 'Microsoft.Web/sites/config@2023-01-01' = {
  name: '${externalFunctionAppName}/appsettings'
  dependsOn: [
    externalFunction
    externalKvAccess
  ]
  properties: {
    AzureWebJobsStorage: '@Microsoft.KeyVault(SecretUri=${secretStorageConn.properties.secretUri})'
    AZURE_STORAGE_CONNECTION_STRING: '@Microsoft.KeyVault(SecretUri=${secretStorageConn.properties.secretUri})'
    APPLICATIONINSIGHTS_CONNECTION_STRING: '@Microsoft.KeyVault(SecretUri=${secretAppInsightsConn.properties.secretUri})'
    FUNCTIONS_EXTENSION_VERSION: '~4'
    FUNCTIONS_WORKER_RUNTIME: 'python'
    BLOB_CONTAINER: blobContainerName
  }
}
```

- [ ] **Step 2: Verify Bicep syntax**

Run: `az bicep build --file deploy/bicep/main.bicep`
Expected: zero errors. Bicep may print a `prefer-symbolic-names` lint warning about the `'${...}/appsettings'` slash naming — that's expected; the slash form is required when the parent is referenced indirectly.

- [ ] **Step 3: Commit**

```bash
git add deploy/bicep/main.bicep
git commit -m "$(cat <<'EOF'
feat(deploy): app settings sub-resources with KV references (REH-48)

Both function apps get Microsoft.Web/sites/config sub-resources with
app settings that reference KV secrets via @Microsoft.KeyVault(SecretUri=...)
syntax. Sub-resources dependsOn the function-app module + role
assignment so KV refs always resolve after access is granted.

Non-secret settings (FUNCTIONS_EXTENSION_VERSION, FUNCTIONS_WORKER_RUNTIME,
LEAGUE_INDEX, DRY_RUN, AGGRESSIVE, BLOB_CONTAINER) stay as plain
values. AGGRESSIVE is now explicit instead of relying on function_app.py's
default.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 6: Create `main.bicepparam`

**Files:**

- Create: `deploy/bicep/main.bicepparam`

- [ ] **Step 1: Create the parameter file**

Create `deploy/bicep/main.bicepparam`:

```bicep
using 'main.bicep'

// Existing prod resource names — preserved for in-place adoption.
param storageAccountName = 'strehoboam6490'
param appServicePlanName = 'GermanyWestCentralLinuxDynamicPlan'
param appInsightsName = 'func-rehoboam'

// Stable configuration.
param blobContainerName = 'rehoboam-data'
param tradingFunctionAppName = 'func-rehoboam'
param externalFunctionAppName = 'func-rehoboam-external'

// App behavior flags.
param leagueIndex = '0'
param dryRun = 'true'
param aggressiveMode = 'true'

// Secrets (kickbaseEmail, kickbasePassword) overridden via CLI -p flags from deploy.sh.
// keyVaultName uses Bicep default (kv-rehoboam-<uniqueString>).
```

- [ ] **Step 2: Verify it compiles**

Run: `az bicep build-params --file deploy/bicep/main.bicepparam`
Expected: zero errors. Produces `deploy/bicep/main.parameters.json` as a side effect — also gitignored by our pattern from Task 1.

If errors mention missing required parameters (kickbaseEmail, kickbasePassword): that's expected — they're @secure() params, passed via CLI override, NOT included in the .bicepparam file.

- [ ] **Step 3: Commit**

```bash
git add deploy/bicep/main.bicepparam
git commit -m "$(cat <<'EOF'
feat(deploy): main.bicepparam with existing prod names (REH-48)

Non-secret defaults: storage account, plan, AI, function app names
all point at the EXISTING prod resources for in-place adoption.
Secrets (kickbaseEmail, kickbasePassword) are NOT in this file —
they're passed via -p CLI overrides from deploy.sh.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

# Phase 2 — Deploy script + cleanup

## Task 7: Create `deploy/deploy.sh`

**Files:**

- Create: `deploy/deploy.sh`

- [ ] **Step 1: Create the deploy script**

Create `deploy/deploy.sh`:

```bash
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
      --parameters kickbaseEmail="$KICKBASE_EMAIL" kickbasePassword="$KICKBASE_PASSWORD"
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
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x deploy/deploy.sh`

- [ ] **Step 3: Verify shellcheck (if installed)**

Run: `command -v shellcheck >/dev/null 2>&1 && shellcheck deploy/deploy.sh || echo "shellcheck not installed, skipping"`
Expected: either zero output (shellcheck passed) or the skip message.

If shellcheck reports issues: fix them. Common: missing quotes around variables, unused variables.

- [ ] **Step 4: Commit**

```bash
git add deploy/deploy.sh
git commit -m "$(cat <<'EOF'
feat(deploy): new deploy.sh with all/infra/code subcommands (REH-48)

Replaces the imperative deploy_azure.sh. Wraps Bicep deployment +
func azure functionapp publish in three positional commands:
- 'all' (default): infra then code for both function apps
- 'infra [--what-if]': just Bicep, optionally preview
- 'code [trading|external]': just publish, optionally narrow to one app

The 'external' function source dir doesn't exist yet (added in
REH-41 P2 Task 2.14). publish_function() gracefully skips missing dirs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 8: Delete `deploy/deploy_azure.sh`

**Files:**

- Delete: `deploy/deploy_azure.sh`

- [ ] **Step 1: Confirm no other references**

Run: `grep -rn "deploy_azure.sh" --include="*.py" --include="*.md" --include="*.sh" --include="*.toml" --include="*.yml" --include="*.yaml" . 2>&1 | grep -v -E "\\.venv|node_modules|__pycache__" | head -10`

Expected output: hopefully only references in CLAUDE.md (which we'll update in Task 9) and possibly in `deploy/deploy_azure.sh` itself.

If there are references in other files we didn't expect, list them and update them in Task 9 alongside CLAUDE.md.

- [ ] **Step 2: Delete the script**

Run: `git rm deploy/deploy_azure.sh`

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
chore(deploy): remove legacy deploy_azure.sh (REH-48)

Bicep-based deploy.sh fully replaces this. Rollback path documented
in the design spec: revert this PR + rerun the old script from the
previous commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

## Task 9: Update CLAUDE.md "Common Commands"

**Files:**

- Modify: `CLAUDE.md`

- [ ] **Step 1: Find the deploy-related section**

Run: `grep -n "deploy_azure\|deploy/deploy" CLAUDE.md | head -10`

Identify the section(s) that need updating. There are likely 1-2 mentions in the "Common Commands" or "Prod-state debugging workflow" sections.

- [ ] **Step 2: Replace references**

Use the Edit tool to make these replacements in `CLAUDE.md`:

Find: `bash deploy/deploy_azure.sh` (any occurrence)
Replace with: `bash deploy/deploy.sh`

If the existing text describes the OLD script's behavior, also update the narrative. Look for sentences like "Deploys to Azure Functions" or similar — update to reflect that Bicep is now used.

In the "Common Commands" section, add (or replace) a deploy block:

```markdown
# Azure deployment (Bicep-based, REH-48)
bash deploy/deploy.sh                  # provision + publish both function apps
bash deploy/deploy.sh infra --what-if  # preview Bicep changes (run before first migration)
bash deploy/deploy.sh infra            # just Bicep deploy
bash deploy/deploy.sh code trading     # publish trading function only
bash deploy/deploy.sh code external    # publish external-refresh function only (after REH-41 P2 lands)
```

If the "Prod-state debugging workflow" section references `deploy_azure.sh`, leave the rest of that workflow intact — just swap the script name.

- [ ] **Step 3: Verify no stale references remain**

Run: `grep -n "deploy_azure" CLAUDE.md`
Expected: zero matches.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: update CLAUDE.md deploy commands for Bicep flow (REH-48)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

______________________________________________________________________

# Phase 3 — Local validation

## Task 10: Local Bicep build + what-if preview

**Files:** none modified

- [ ] **Step 1: Full Bicep build**

Run: `az bicep build --file deploy/bicep/main.bicep`
Expected: zero errors. Linter warnings about `prefer-symbolic-names` for the appsettings sub-resource slash naming are OK.

- [ ] **Step 2: Bicepparam validation**

Run: `az bicep build-params --file deploy/bicep/main.bicepparam`
Expected: zero errors. If errors mention missing `kickbaseEmail`/`kickbasePassword`: those are CLI-only params, that's expected.

- [ ] **Step 3: What-if preview against prod**

Source the environment to get credentials:

```bash
set -a; source .env; set +a
```

Run the what-if:

```bash
bash deploy/deploy.sh infra --what-if 2>&1 | tee /tmp/reh-48-whatif.log
```

Expected output structure (Azure prints colorized diffs):

- **Modify**: existing 5 resources (`strehoboam6490`, `rehoboam-data` container, `GermanyWestCentralLinuxDynamicPlan`, `func-rehoboam` site, `func-rehoboam` insights component). Tightening of TLS + public blob access on storage; addition of system-assigned identity to `func-rehoboam`; default property fills elsewhere.

- **Create**: `kv-rehoboam-<hash>`, 4 KV secrets, 2 role assignments, `func-rehoboam-external` site, 2 `appsettings` sub-resources.

- **Delete**: NONE.

- [ ] **Step 4: Manual review of what-if output**

Open `/tmp/reh-48-whatif.log`. Scan for:

- Any line starting with `- Delete` — STOP and investigate before proceeding. The Bicep template should never trigger a delete in Incremental mode unless we got an existing resource name wrong.
- Any modification to a resource we didn't intend to touch.

If everything looks safe, save a summary of the what-if output for the PR description in Phase 4.

- [ ] **Step 5: Do NOT commit**

The /tmp log is for human review only. No code changes in this task.

______________________________________________________________________

# Phase 4 — Production cutover (user-gated)

> **Phase 4 tasks modify production infrastructure.** The subagent executing Phase 4 should STOP after running each prod-touching command and report the result to the controller. The controller surfaces the result to the user and asks for explicit go-ahead for the next step.

## Task 11: Apply Bicep to prod (user-confirmed)

**Files:** none modified

- [ ] **Step 1: Confirm preconditions**

Verify:

- Working tree is clean (`git status` shows only the expected commits on `reh-48-bicep`)

- You're authenticated to Azure: `az account show --query "{name:name, id:id}" -o tsv`

- The trading function is currently STOPPED (post-season state): `az functionapp show -n func-rehoboam -g rg-rehoboam --query state -o tsv` should print `Stopped`. If it prints `Running`, STOP first: `az functionapp stop -n func-rehoboam -g rg-rehoboam`

- The what-if output from Task 10 was reviewed and has no DELETE entries

- [ ] **Step 2: STOP and ask the user for explicit confirmation**

This step modifies prod. Report to the controller:

> "Ready to deploy Bicep to prod resource group rg-rehoboam. Preconditions confirmed (clean tree, az authenticated, function stopped, what-if showed no deletes). Awaiting user confirmation to run `bash deploy/deploy.sh infra`."

Wait for the controller's explicit go-ahead before running the deploy.

- [ ] **Step 3: Apply Bicep**

Run: `bash deploy/deploy.sh infra 2>&1 | tee /tmp/reh-48-infra-deploy.log`
Expected runtime: 2-5 minutes for first migration. Successive deploys are faster.

If `az deployment group create` exits non-zero, read the error carefully:

- **`Conflict: ... already exists with different properties`**: a resource has property drift (e.g. existing TLS version mismatch). Note the offending resource and decide whether to widen the Bicep param or accept the upgrade.
- **`AuthorizationFailed`**: missing role on the deployer principal. STOP and surface to user.
- **`KeyVaultNameNotAvailable`**: rare — `uniqueString(resourceGroup().id)` collided globally. Add an explicit `keyVaultName` override to `main.bicepparam`.

If deploy succeeds, the output table will list each resource and its operation. Save this to the deploy log file.

- [ ] **Step 4: Verify Phase 1 of the post-migration checks**

Run each:

```bash
KV_NAME=$(az deployment group show -g rg-rehoboam -n main --query 'properties.outputs.keyVaultName.value' -o tsv)
echo "KV: $KV_NAME"

# 1. 4 KV secrets
az keyvault secret list --vault-name "$KV_NAME" -o table

# 2. Managed identities
az functionapp show -n func-rehoboam -g rg-rehoboam --query identity -o json
az functionapp show -n func-rehoboam-external -g rg-rehoboam --query identity -o json

# 3. Role assignments
az role assignment list --scope "$(az keyvault show -n "$KV_NAME" -g rg-rehoboam --query id -o tsv)" -o table

# 4. KV refs in app settings
az functionapp config appsettings list -n func-rehoboam -g rg-rehoboam \
  --query "[?contains(value, 'Microsoft.KeyVault')].name" -o tsv
```

Expected:

- 4 secrets listed
- Both function apps have `type=SystemAssigned` + a `principalId`
- 2 role assignments listed, role "Key Vault Secrets User", scope is the KV
- Settings listed: `KICKBASE_EMAIL, KICKBASE_PASSWORD, AzureWebJobsStorage, AZURE_STORAGE_CONNECTION_STRING, APPLICATIONINSIGHTS_CONNECTION_STRING`

If anything is missing, STOP and report.

- [ ] **Step 5: Report back to controller**

Summarize:

- Bicep deploy succeeded / failed (with error)
- All 4 verification checks passed (or which failed)
- KV name from the deploy output

No commit here — Phase 4 doesn't change repo files.

______________________________________________________________________

## Task 12: Publish trading function code (user-confirmed)

**Files:** none modified

- [ ] **Step 1: STOP and ask the user**

Report:

> "Bicep applied successfully. Function app `func-rehoboam` currently has KV-referenced app settings but no deployed code (Bicep wiped WEBSITE_RUN_FROM_PACKAGE). Ready to re-publish the trading function code. Awaiting user confirmation to run `bash deploy/deploy.sh code trading`."

Wait for go-ahead.

- [ ] **Step 2: Publish the trading function**

Run: `bash deploy/deploy.sh code trading 2>&1 | tee /tmp/reh-48-publish.log`
Expected runtime: 1-3 minutes. Output shows `func` packaging the code and uploading it.

Common errors:

- **`AzureWebJobsStorage is not set`**: KV reference hasn't propagated yet, or role assignment isn't propagated. Wait 60s and retry.

- **`Could not find a function app named...`**: name mismatch, check Bicep param.

- [ ] **Step 3: Briefly start + verify**

Start the function and check it can resolve KV refs:

```bash
az functionapp start -n func-rehoboam -g rg-rehoboam
sleep 30  # let the app initialize

# Check for KV resolution errors in App Insights (last 5 min)
az monitor app-insights events show \
  --app func-rehoboam \
  --resource-group rg-rehoboam \
  --type exceptions \
  --offset PT5M \
  --query "value[?contains(@.exception.parsedStack[*].assembly | join(' ', @), 'KeyVault') || contains(@.exception.outerType, 'KeyVault')]" \
  -o json
```

Expected: empty array `[]` (zero KeyVault-related exceptions). If the `az monitor app-insights events show` command fails because the App Insights extension isn't installed, run instead: `az extension add --name application-insights` then retry.

If exceptions appear: capture them and STOP. Likely cause is role assignment not propagated — wait 60s and recheck.

- [ ] **Step 4: Stop the function (back to off, since trading is paused for the season)**

```bash
az functionapp stop -n func-rehoboam -g rg-rehoboam
```

This matches the pre-migration state — the bot is stopped for the off-season per the post-mortem decision.

- [ ] **Step 5: Report back to controller**

Summarize:

- Code publish succeeded / failed
- Function started without KV resolution errors
- Function back to stopped state

No commit.

______________________________________________________________________

## Task 13: Smoke + open PR (user-confirmed)

**Files:** none modified (commits already made; this is push + PR)

- [ ] **Step 1: STOP and ask the user**

Report:

> "Migration complete and verified locally. Ready to push branch + open PR. Awaiting user confirmation."

Wait for go-ahead.

- [ ] **Step 2: Push the branch**

Run: `git push -u origin reh-48-bicep`

- [ ] **Step 3: Open the PR**

Capture the what-if log and the verification outputs into a single block for the PR description:

```bash
WHATIF_TAIL=$(tail -40 /tmp/reh-48-whatif.log)
DEPLOY_TAIL=$(tail -20 /tmp/reh-48-infra-deploy.log)
```

Then create the PR:

```bash
gh pr create --title "feat(deploy): migrate Azure deployment to Bicep + Key Vault (REH-48)" --body "$(cat <<'EOF'
## Summary

- Replaces `deploy/deploy_azure.sh` (imperative bash) with declarative Bicep (`deploy/bicep/main.bicep` + reusable `modules/function-app.bicep`).
- All sensitive secrets (Kickbase credentials, storage connection string, App Insights connection string) move to Azure Key Vault. Function apps read them via system-assigned managed identity + `Key Vault Secrets User` role.
- Models BOTH function apps in Bicep: existing `func-rehoboam` for trading + new `func-rehoboam-external` for REH-41 Phase 2 weekly external-data refresh. REH-41 P2 Task 2.14 can now just add the second function's code, not its infrastructure.
- New `deploy/deploy.sh` with positional commands: `all` (default) / `infra [--what-if]` / `code [trading|external]`.
- Existing prod resources adopted in place — no DELETE operations.

## Design

Spec at `docs/superpowers/specs/2026-05-18-bicep-migration-design.md`. Implementation plan at `docs/superpowers/plans/2026-05-18-reh-48-bicep-migration-implementation.md`.

## Follow-up tickets

- REH-49: restrict KV public network access (IP allowlist)
- REH-50: replace storage connection string with managed identity for blob access (eliminates the storage account key entirely)

Both depend on this PR landing.

## Test plan

- [x] `az bicep build --file deploy/bicep/main.bicep` — zero errors
- [x] `az bicep build-params --file deploy/bicep/main.bicepparam` — zero errors
- [x] `bash deploy/deploy.sh infra --what-if` — only MODIFY on existing resources, CREATE on new KV/secrets/role-assignments/external-function/appsettings-sub-resources, zero DELETE
- [x] `bash deploy/deploy.sh infra` against prod — succeeded; 4 KV secrets, 2 managed identities, 2 role assignments, KV-referenced app settings confirmed
- [x] `bash deploy/deploy.sh code trading` — function app started without KV resolution errors, then stopped (matches off-season state)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Capture the PR URL**

The `gh pr create` output ends with a URL. Save it.

- [ ] **Step 5: Report to controller**

Summarize:

- Branch pushed
- PR URL: `<paste here>`
- Test plan checklist confirmed

The implementation is complete. The PR can be reviewed and merged by the user when ready.

______________________________________________________________________

## Self-Review Outcomes

Performed after writing the plan:

**Spec coverage**:

- Spec § Acceptance criteria #1 (`az bicep build` zero errors) → covered by Tasks 1-5 (each task ends with `az bicep build`)
- Spec § Acceptance criteria #2 (what-if shows no DELETE) → Task 10 Step 4
- Spec § Acceptance criteria #3 (4 KV secrets, 2 identities, 2 role assignments, KV refs in app settings) → Task 11 Step 4
- Spec § Acceptance criteria #4 (function resumes schedule with no KV errors) → Task 12 Step 3
- Spec § Acceptance criteria #5 (`deploy_azure.sh` deleted) → Task 8
- Spec § Acceptance criteria #6 (CLAUDE.md updated) → Task 9
- Spec § Migration plan Phase A → Task 10
- Spec § Migration plan Phase B-F → Tasks 11-13

All spec sections map to tasks.

**Placeholder scan**: no TBDs, no "implement later". Every code-modifying step shows the exact code. The only deliberate "later" reference is in Task 7 (`publish_function` gracefully skips the external function's source directory because it doesn't exist yet — added in REH-41 P2 Task 2.14). This is by design, documented in the task and in the script's behavior.

**Type consistency**: Bicep parameter names are stable across tasks (`storageAccountName`, `appServicePlanName`, `appInsightsName`, `keyVaultName`, `tradingFunctionAppName`, `externalFunctionAppName`, `leagueIndex`, `dryRun`, `aggressiveMode`, `blobContainerName`, `kickbaseEmail`, `kickbasePassword`). Resource symbolic names (`storageAccount`, `blobService`, `blobContainer`, `appServicePlan`, `appInsights`, `keyVault`, `secretKickbaseEmail`, `secretKickbasePassword`, `secretStorageConn`, `secretAppInsightsConn`, `tradingFunction`, `externalFunction`, `tradingKvAccess`, `externalKvAccess`, `tradingAppSettings`, `externalAppSettings`) are consistent across all tasks that reference them. Module output names (`name`, `principalId`, `functionAppId`) match what the consumer expects.

**Scope check**: 13 tasks total. Tasks 1-6 build Bicep (one focused commit each). Tasks 7-9 ship the deploy script + cleanup. Task 10 is local validation. Tasks 11-13 are user-gated production deploys + PR. The plan ships as one PR.
