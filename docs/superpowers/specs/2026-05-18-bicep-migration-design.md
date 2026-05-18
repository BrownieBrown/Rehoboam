# Bicep migration for Azure deployment (REH-48)

**Status**: Design approved, ready for implementation plan
**Author**: Marco Braun (with Claude)
**Date**: 2026-05-18
**Linear**: REH-48

## Problem

`deploy/deploy_azure.sh` provisions Azure resources imperatively via a long sequence of `az` CLI commands. Drawbacks:

- Non-declarative: a partial run leaves the env in an indeterminate state, and there is no `terraform plan`-equivalent.
- Hard to extend: REH-41 Phase 2 is about to add a SECOND function app (`external_refresh`) and would otherwise duplicate the bash pattern.
- Diverges from convention: other repos use Bicep for the same shape of deployment.
- Secrets (KICKBASE_EMAIL, KICKBASE_PASSWORD, storage account key, AI connection string) currently live as plaintext app settings — visible to anyone with `Reader` on the function app.

## Goals

- Replace the imperative bash provisioning with declarative Bicep templates.
- Move all sensitive app settings to Azure Key Vault. Function apps read them via system-assigned managed identity + KV references.
- Both function apps (existing `func-rehoboam` for trading, new `func-rehoboam-external` for REH-41 Phase 2 weekly refresh) modeled in one Bicep template so REH-41 P2 Task 2.14 just adds a parameterized entry.
- Adopt existing prod resources in place (no destructive recreation).
- Keep `func azure functionapp publish` for code deployment (Bicep does not ship code).

## Non-goals

- `scripts/sync-azure-deps.sh` — build tooling, not infra; stays.
- `deploy/azure_function/{function_app.py, host.json, requirements.txt}` — Functions runtime contracts; stay.
- Migration to Premium plan, or managed identity for blob access (would change auth model from connection-string to identity); follow-up work.
- Bicep `what-if` automation in CI — manual `bash deploy/deploy.sh infra --what-if` is enough for v1.

## Decisions

| Decision                      | Choice                                                                                                       | Rationale                                                                                                                                                         |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bicep structure               | Modular: `main.bicep` + `modules/function-app.bicep`                                                         | One reusable module called twice for the two function apps; shared infra inline in root. Idiomatic for "N function apps sharing infra".                           |
| Plan-name handling            | Keep the auto-generated name (`GermanyWestCentralLinuxDynamicPlan`) as a parameter, defaulting to that value | Zero-downtime adoption of existing prod resources. Aesthetic ugliness is acceptable; future fresh deployments can override the param.                             |
| Secrets handling              | Azure Key Vault, all sensitive settings                                                                      | KICKBASE_EMAIL, KICKBASE_PASSWORD, storage connection string, App Insights connection string. Function apps read via system-assigned managed identity. ~€0.01/mo. |
| Deploy script granularity     | Single `deploy/deploy.sh` with positional commands (`all`/`infra`/`code [trading\|external]`)                | Same UX as old `deploy_azure.sh` but flexible. Most deploys want `all`.                                                                                           |
| Sub-resource for app settings | Separate `Microsoft.Web/sites/config` per function app, NOT inline `siteConfig.appSettings`                  | Required so app settings can `dependsOn` the role assignment — otherwise KV references could fail to resolve at first function startup.                           |

## Acceptance criteria

- [ ] `az bicep build --file deploy/bicep/main.bicep` succeeds with zero errors.
- [ ] `bash deploy/deploy.sh infra --what-if` against existing `rg-rehoboam` shows: MODIFY on existing 5 resources, CREATE on Key Vault + 4 secrets + 2 role assignments + new external function app + 2 appsettings sub-resources, DELETE on nothing.
- [ ] After `bash deploy/deploy.sh infra` against prod:
  - 4 KV secrets exist (`kickbase-email`, `kickbase-password`, `storage-connection-string`, `app-insights-connection-string`)
  - Both function apps have system-assigned managed identities
  - 2 role assignments exist (Key Vault Secrets User on each function principal, scoped to the KV)
  - Function app settings reference KV via `@Microsoft.KeyVault(SecretUri=...)` for all sensitive values
- [ ] After `bash deploy/deploy.sh code`, `func-rehoboam` resumes its 08:00/20:00 UTC schedule (if started), reads secrets transparently from KV, no "Key Vault Reference resolution error" in App Insights.
- [ ] `deploy/deploy_azure.sh` is deleted.
- [ ] CLAUDE.md "Common Commands" updated with new deploy commands.

## Architecture

### File structure

```
deploy/
├── bicep/
│   ├── main.bicep              # root: shared infra (storage, plan, AI, KV) + 2x module call + role assignments + appsettings sub-resources
│   ├── main.bicepparam         # non-secret defaults (resource names, region, app config flags)
│   └── modules/
│       └── function-app.bicep  # reusable: a single function-app site + managed identity (no app settings)
├── deploy.sh                   # NEW: provisions via Bicep, publishes code via func tools
└── azure_function/             # unchanged (function code)
    ├── function_app.py
    ├── host.json
    └── requirements.txt

deploy/azure_function_external_refresh/   # added LATER in REH-41 P2 Task 2.14
    ├── function_app.py
    ├── host.json
    └── requirements.txt
```

Removed: `deploy/deploy_azure.sh` (deleted in this PR).

### Bicep dependency DAG

```
storage account ──┐
                  ├──> blob container
                  └──> storage-connection-string (KV secret)

app service plan

app insights ──> app-insights-connection-string (KV secret)

KV ──> 4 secrets (above 2 plus kickbase-email, kickbase-password from @secure() params)

function-app module (trading) ──> trading-kv-access role assignment ──┐
function-app module (external) ──> external-kv-access role assignment ┤
                                                                        ├──> trading appsettings sub-resource (KV references)
                                                                        └──> external appsettings sub-resource (KV references)
```

### `main.bicep` (root template)

Parameters:

- Resource names: `storageAccountName` (no default — must pass `strehoboam6490` for adoption), `appServicePlanName` (default `GermanyWestCentralLinuxDynamicPlan`), `appInsightsName` (default `func-rehoboam`), `tradingFunctionAppName` (default `func-rehoboam`), `externalFunctionAppName` (default `func-rehoboam-external`), `keyVaultName` (default `kv-rehoboam-${uniqueString(resourceGroup().id)}`)
- Behavior flags: `leagueIndex` (`'0'`), `dryRun` (`'true'`), `aggressiveMode` (`'true'`), `blobContainerName` (`'rehoboam-data'`)
- Secrets: `@secure() param kickbaseEmail`, `@secure() param kickbasePassword`
- `location`: defaults to `resourceGroup().location`

Resources (created in dependency order via Bicep DAG):

1. `Microsoft.Storage/storageAccounts` — `Standard_LRS`, `StorageV2`, TLS 1.2, no public blob access
1. `Microsoft.Storage/storageAccounts/blobServices/containers` — `rehoboam-data`
1. `Microsoft.Web/serverfarms` — `Y1` Dynamic, `kind: linux`, `reserved: true`
1. `Microsoft.Insights/components` — `kind: web`, `Application_Type: web`
1. `Microsoft.KeyVault/vaults` — RBAC-authorized, no public network restrictions
1. 4 × `Microsoft.KeyVault/vaults/secrets`:
   - `kickbase-email` (from `@secure() param kickbaseEmail`)
   - `kickbase-password` (from `@secure() param kickbasePassword`)
   - `storage-connection-string` (computed from `storageAccount.listKeys().keys[0].value`)
   - `app-insights-connection-string` (from `appInsights.properties.ConnectionString`)
1. 2 × module call `modules/function-app.bicep` — creates the trading and external function apps, each with `identity: { type: 'SystemAssigned' }`
1. 2 × `Microsoft.Authorization/roleAssignments` at scope=keyVault — grants `Key Vault Secrets User` (role definition ID `4633458b-17de-408a-b874-0445c86b69e6`) to each function app's principal
1. 2 × `Microsoft.Web/sites/config@2023-01-01` named `${functionAppName}/appsettings`, `dependsOn` the corresponding role assignment, containing the full app-settings dictionary with KV references

### `modules/function-app.bicep` (reusable module)

Parameters: `name`, `location`, `appServicePlanId`.

Resources:

- `Microsoft.Web/sites@2023-01-01` with `kind: 'functionapp,linux'`, `identity: { type: 'SystemAssigned' }`, `httpsOnly: true`. `siteConfig`: `linuxFxVersion: 'Python|3.11'`, `ftpsState: 'FtpsOnly'`, `minTlsVersion: '1.2'`, `alwaysOn: false`. No app settings — those are added in `main.bicep`.

Outputs: `name`, `principalId`, `functionAppId`.

### App settings dictionary

For `func-rehoboam` (trading):

| Setting                                 | Value                                       |
| --------------------------------------- | ------------------------------------------- |
| `AzureWebJobsStorage`                   | KV ref: `storage-connection-string`         |
| `AZURE_STORAGE_CONNECTION_STRING`       | KV ref: `storage-connection-string`         |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | KV ref: `app-insights-connection-string`    |
| `KICKBASE_EMAIL`                        | KV ref: `kickbase-email`                    |
| `KICKBASE_PASSWORD`                     | KV ref: `kickbase-password`                 |
| `FUNCTIONS_EXTENSION_VERSION`           | `~4` (plain)                                |
| `FUNCTIONS_WORKER_RUNTIME`              | `python` (plain)                            |
| `LEAGUE_INDEX`                          | `leagueIndex` parameter value (plain)       |
| `DRY_RUN`                               | `dryRun` parameter value (plain)            |
| `AGGRESSIVE`                            | `aggressiveMode` parameter value (plain)    |
| `BLOB_CONTAINER`                        | `blobContainerName` parameter value (plain) |

For `func-rehoboam-external` (REH-41 P2 refresh):

| Setting                                 | Value                                       |
| --------------------------------------- | ------------------------------------------- |
| `AzureWebJobsStorage`                   | KV ref: `storage-connection-string`         |
| `AZURE_STORAGE_CONNECTION_STRING`       | KV ref: `storage-connection-string`         |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | KV ref: `app-insights-connection-string`    |
| `FUNCTIONS_EXTENSION_VERSION`           | `~4` (plain)                                |
| `FUNCTIONS_WORKER_RUNTIME`              | `python` (plain)                            |
| `BLOB_CONTAINER`                        | `blobContainerName` parameter value (plain) |

Notes:

- KV reference syntax: `@Microsoft.KeyVault(SecretUri=${secretResource.properties.secretUri})`. Function app's runtime resolves this to the actual secret value at startup.
- `WEBSITE_RUN_FROM_PACKAGE` and `WEBSITE_ENABLE_SYNC_UPDATE_SITE` are NOT in Bicep — `func azure functionapp publish` adds them during code deployment.

### `main.bicepparam`

```bicep
using 'main.bicep'

param storageAccountName = 'strehoboam6490'
param appServicePlanName = 'GermanyWestCentralLinuxDynamicPlan'
param appInsightsName = 'func-rehoboam'
param blobContainerName = 'rehoboam-data'
param tradingFunctionAppName = 'func-rehoboam'
param externalFunctionAppName = 'func-rehoboam-external'

param leagueIndex = '0'
param dryRun = 'true'
param aggressiveMode = 'true'
```

Secrets (`kickbaseEmail`, `kickbasePassword`) are passed via CLI `-p` overrides from `deploy.sh`.

### `deploy/deploy.sh`

```bash
#!/bin/bash
# Bicep-based Azure deployment for Rehoboam.
#
# Usage:
#   bash deploy/deploy.sh                  # provision + publish both function apps
#   bash deploy/deploy.sh infra            # just Bicep deploy (no code publish)
#   bash deploy/deploy.sh infra --what-if  # preview Bicep changes, no apply
#   bash deploy/deploy.sh code             # just func publish both
#   bash deploy/deploy.sh code trading     # publish trading function only
#   bash deploy/deploy.sh code external    # publish external function only

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

  echo "==> Deploying Bicep template to $RESOURCE_GROUP..."
  az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
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
  echo "==> Publishing $app_name from $src_dir..."

  local deploy_dir
  deploy_dir="$(mktemp -d)"
  cp "$src_dir"/{function_app.py,host.json,requirements.txt} "$deploy_dir/"
  cp -r "$PROJECT_ROOT/rehoboam" "$deploy_dir/"
  cp "$PROJECT_ROOT/pyproject.toml" "$PROJECT_ROOT/README.md" "$deploy_dir/"

  (cd "$deploy_dir" && func azure functionapp publish "$app_name" --python)
  rm -rf "$deploy_dir"
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

Caveat documented in the help text: `bash deploy/deploy.sh infra` alone WIPES the `WEBSITE_RUN_FROM_PACKAGE` app setting that `func azure functionapp publish` adds — running `infra` without a follow-up `code` deploy leaves the function in a "no deployed code" state. Always run `all` (the default) or follow `infra` with `code` for a healthy end-state.

## Migration plan (production cutover)

### Phase A — Preview

```bash
git checkout -b reh-48-bicep
az bicep build --file deploy/bicep/main.bicep
bash deploy/deploy.sh infra --what-if
```

Reviewer confirms what-if output:

- MODIFY on existing 5 resources (storage account, blob container, app service plan, function app, App Insights)
- CREATE on: KV, 4 secrets, 2 role assignments, new external function app, 2 appsettings sub-resources
- DELETE on: NONE

If unexpected DELETE entries appear, STOP and investigate before applying.

### Phase B — Apply infrastructure

```bash
bash deploy/deploy.sh infra
```

The function app `func-rehoboam` is currently STOPPED (post-season) so there is no in-flight traffic risk during the cutover.

### Phase C — Verify

```bash
# 1. Secrets in KV
az keyvault secret list --vault-name $(az deployment group show -g rg-rehoboam -n main --query 'properties.outputs.keyVaultName.value' -o tsv) -o table
# Expect: 4 secrets

# 2. Managed identities
az functionapp show -n func-rehoboam -g rg-rehoboam --query identity -o json
az functionapp show -n func-rehoboam-external -g rg-rehoboam --query identity -o json
# Expect: both have SystemAssigned + principalId

# 3. Role assignments
az role assignment list --scope $(az keyvault show -n $(az deployment group show -g rg-rehoboam -n main --query 'properties.outputs.keyVaultName.value' -o tsv) -g rg-rehoboam --query id -o tsv) -o table
# Expect: 2 entries, role "Key Vault Secrets User"

# 4. App settings KV refs
az functionapp config appsettings list -n func-rehoboam -g rg-rehoboam --query "[?contains(value, 'Microsoft.KeyVault')].name" -o tsv
# Expect: KICKBASE_EMAIL, KICKBASE_PASSWORD, AzureWebJobsStorage,
#         AZURE_STORAGE_CONNECTION_STRING, APPLICATIONINSIGHTS_CONNECTION_STRING
```

### Phase D — Publish code

```bash
bash deploy/deploy.sh code trading
```

Re-publishes the (unchanged) trading function code. This restores `WEBSITE_RUN_FROM_PACKAGE` and uploads the package.

### Phase E — Smoke

```bash
az functionapp start -n func-rehoboam -g rg-rehoboam
# Wait ~30s
az monitor app-insights events show --app func-rehoboam -g rg-rehoboam --type exceptions --offset PT5M
# Expect: no "Key Vault Reference resolution error"
az functionapp stop -n func-rehoboam -g rg-rehoboam   # back to off; trading resumes when REH-41 P2 lands
```

### Phase F — Cleanup

```bash
git rm deploy/deploy_azure.sh
# update CLAUDE.md "Common Commands" section
git add CLAUDE.md
git commit -m "chore: remove legacy bash deploy, document Bicep flow"
```

## Testing strategy

### In-PR validation

- `az bicep build --file deploy/bicep/main.bicep` — syntax validation. Add to PR description's checklist.
- Bicep what-if output — paste summary into PR description. Reviewer manually confirms no DELETE entries.

### Adoption smoke (manual, single shot)

- Phase A through Phase E above, run once against prod RG.
- Captures any property-mismatch errors (e.g. existing storage account in different SKU than Bicep declares).

### No Python test changes

The migration touches infrastructure files only. `rehoboam/` Python is untouched. Existing test suite is unaffected.

## Error handling

| Failure mode                                                            | Behavior                                                                                                                                                   |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `az bicep build` fails                                                  | Syntax error. Fix the Bicep template, retry.                                                                                                               |
| What-if shows unexpected DELETE                                         | STOP. Investigate. Likely cause: parameter mismatch with existing resource name.                                                                           |
| `az deployment group create` errors mid-deploy                          | Azure leaves partial state. Bicep is idempotent — re-run the deploy command, it picks up from where it stopped.                                            |
| `func publish` reports `WEBSITE_RUN_FROM_PACKAGE` was reset             | Expected — Bicep deploy wipes app settings dict. `func publish` re-adds it. End-state healthy.                                                             |
| Function app fails to start with "Key Vault Reference resolution error" | Means role assignment not yet propagated OR KV secret missing. Wait 60s and retry; if persistent, check role assignment was created and principal matches. |
| KV name globally conflicts                                              | The `uniqueString(resourceGroup().id)` suffix should make this rare. If it happens, override `keyVaultName` parameter explicitly.                          |

## Rollback plan

If something breaks post-migration and the bot needs to be deployable via the old flow:

```bash
git checkout main   # before this PR was merged (or wherever deploy_azure.sh still exists)
bash deploy/deploy_azure.sh   # re-applies plain (non-KV) app settings, overwrites Bicep's KV refs
```

The KV resource and stored secrets remain (harmless — they cost ~€0.01/mo). The function app's appsettings get overwritten back to plaintext values. Recovery time: ~5 minutes.

After Bicep PR merges and `deploy_azure.sh` is deleted, this rollback requires a `git revert` of the PR plus `bash deploy_azure.sh` from the reverted state.

## Implementation order

1. Create branch `reh-48-bicep` from main.
1. Create `deploy/bicep/main.bicep` skeleton (shared infra only, no function apps yet).
1. Run `az bicep build` — confirm syntax.
1. Add `modules/function-app.bicep` and 2x module calls.
1. Add KV + secrets + role assignments + appsettings sub-resources.
1. Create `main.bicepparam` with existing prod names.
1. Create `deploy/deploy.sh` and chmod +x.
1. Delete `deploy/deploy_azure.sh`.
1. Update CLAUDE.md.
1. Local validation: `az bicep build`, `bash deploy/deploy.sh infra --what-if`.
1. Manual prod adoption: phases A-F above.
1. PR open, manual smoke evidence in PR description.

## Open questions

None at this point. All design decisions are locked.
