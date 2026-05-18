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

// Secrets: OVERRIDE these via CLI -p flags from deploy.sh — never commit real values here.
// Example: az deployment group create ... -p kickbaseEmail=you@example.com kickbasePassword=secret
param kickbaseEmail = ''
param kickbasePassword = ''

// keyVaultName uses Bicep default (kv-rehoboam-<uniqueString>).
