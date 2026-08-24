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
//
// dryRun and aggressiveMode are deliberately NOT set here. The app settings are
// a declarative Microsoft.Web/sites/config resource, so Bicep replaces the whole
// collection on every `deploy.sh infra` — a value hard-coded in this file
// silently overwrites whatever production is actually running.
//
// That happened on 2026-08-24: an infra deploy to install Telegram secrets reset
// DRY_RUN to 'true' on the live bot. It kept starting sessions and reporting
// success while placing no bids and sending no proposals, which is the worst
// shape of failure — indistinguishable from a quiet market. deploy.sh now
// requires DRY_RUN in .env and passes it through, so the deployed value always
// reflects a stated intent rather than a default nobody looked at.
param leagueIndex = '0'

// Secrets: OVERRIDE these via CLI -p flags from deploy.sh — never commit real values here.
// Example: az deployment group create ... -p kickbaseEmail=you@example.com kickbasePassword=secret
param kickbaseEmail = ''
param kickbasePassword = ''

// keyVaultName uses Bicep default (kv-reh-<uniqueString>, 20 chars, fits the 24-char KV limit).
