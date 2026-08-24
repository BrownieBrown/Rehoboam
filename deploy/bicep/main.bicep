targetScope = 'resourceGroup'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Globally-unique storage account name. EXISTING prod: strehoboam6490.')
@minLength(3)
@maxLength(24)
param storageAccountName string

@description('App Service Plan name. EXISTING prod (auto-generated): GermanyWestCentralLinuxDynamicPlan.')
param appServicePlanName string = 'GermanyWestCentralLinuxDynamicPlan'

@description('Application Insights name. EXISTING prod: func-rehoboam.')
param appInsightsName string = 'func-rehoboam'

@description('Blob container name for the bot state SQLite DBs + external/ JSON cache.')
param blobContainerName string = 'rehoboam-data'

@description('Trading-session function app name.')
param tradingFunctionAppName string = 'func-rehoboam'

@description('External-data refresh function app name (REH-41 Phase 2).')
param externalFunctionAppName string = 'func-rehoboam-external'

@description('Key Vault name. Must be globally unique (3-24 chars, alphanumeric + hyphens). uniqueString returns 13 chars, leaving 11 for the prefix.')
param keyVaultName string = 'kv-reh-${uniqueString(resourceGroup().id)}'

@description('Kickbase login email.')
@secure()
param kickbaseEmail string

@description('Kickbase login password.')
@secure()
param kickbasePassword string

@description('Telegram bot token for trade-approval messages. Empty disables Telegram entirely.')
@secure()
param telegramBotToken string = ''

@description('Telegram chat to send trade proposals to.')
@secure()
param telegramChatId string = ''

@description('Shared secret Telegram echoes in X-Telegram-Bot-Api-Secret-Token. The approval webhook is a public endpoint that spends money, so a callback without this header is rejected before anything is read from it.')
@secure()
param telegramWebhookSecret string = ''

@description('SMTP host for the daily summary email. Empty disables email.')
param smtpHost string = ''

@description('SMTP port; 587 for STARTTLS.')
param smtpPort string = '587'

@description('SMTP username. Also used as the From address.')
@secure()
param smtpUser string = ''

@description('SMTP password or app password.')
@secure()
param smtpPassword string = ''

@description('Recipient of the daily summary email.')
param alertEmailTo string = ''

@description('League index in the Kickbase leagues list.')
param leagueIndex string = '0'

@description('When true, trading function simulates trades without placing them.')
param dryRun string = 'true'

@description('When true, trading function uses aggressive thresholds.')
param aggressiveMode string = 'true'

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

// Optional secrets: created only when supplied, because Key Vault rejects an
// empty secret value. The matching app settings are added by the same
// condition below, so an unconfigured channel is genuinely absent rather than
// present-but-blank.
resource secretTelegramBotToken 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(telegramBotToken)) {
  parent: keyVault
  name: 'telegram-bot-token'
  properties: { value: telegramBotToken }
}

resource secretTelegramChatId 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(telegramChatId)) {
  parent: keyVault
  name: 'telegram-chat-id'
  properties: { value: telegramChatId }
}

resource secretTelegramWebhookSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(telegramWebhookSecret)) {
  parent: keyVault
  name: 'telegram-webhook-secret'
  properties: { value: telegramWebhookSecret }
}

resource secretSmtpUser 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(smtpUser)) {
  parent: keyVault
  name: 'smtp-user'
  properties: { value: smtpUser }
}

resource secretSmtpPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(smtpPassword)) {
  parent: keyVault
  name: 'smtp-password'
  properties: { value: smtpPassword }
}

// Built from the vault URI rather than from the conditional resources above,
// so referencing them is never evaluated when they were not created.
var kvSecretPrefix = '${keyVault.properties.vaultUri}secrets/'

var telegramSettings = empty(telegramBotToken) ? {} : {
  TELEGRAM_BOT_TOKEN: '@Microsoft.KeyVault(SecretUri=${kvSecretPrefix}telegram-bot-token)'
  TELEGRAM_CHAT_ID: '@Microsoft.KeyVault(SecretUri=${kvSecretPrefix}telegram-chat-id)'
  TELEGRAM_WEBHOOK_SECRET: '@Microsoft.KeyVault(SecretUri=${kvSecretPrefix}telegram-webhook-secret)'
}

var smtpSettings = empty(smtpHost) ? {} : {
  SMTP_HOST: smtpHost
  SMTP_PORT: smtpPort
  SMTP_USER: '@Microsoft.KeyVault(SecretUri=${kvSecretPrefix}smtp-user)'
  SMTP_PASSWORD: '@Microsoft.KeyVault(SecretUri=${kvSecretPrefix}smtp-password)'
  ALERT_EMAIL_TO: alertEmailTo
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

// ---------------------------------------------------------------------------
// Role assignments — Key Vault Secrets User for each function's managed identity
// ---------------------------------------------------------------------------

// Built-in role: "Key Vault Secrets User"
// https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles#key-vault-secrets-user
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

// guid() seed uses the function app NAME instead of principalId because
// principalId is a runtime value (BCP120). Using a deploy-time stable
// seed keeps the role assignment name deterministic + idempotent.

resource tradingKvAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, tradingFunctionAppName, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: tradingFunction.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

resource externalKvAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, externalFunctionAppName, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: externalFunction.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// App settings — separate Microsoft.Web/sites/config sub-resources so they
// can dependsOn the role assignment. KV references resolve at runtime only
// after the function's managed identity has Get permission on the secrets.
// ---------------------------------------------------------------------------

resource tradingAppSettings 'Microsoft.Web/sites/config@2023-01-01' = {
  name: '${tradingFunctionAppName}/appsettings'
  dependsOn: [
    tradingKvAccess
  ]
  properties: union({
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
  }, telegramSettings, smtpSettings)
}

resource externalAppSettings 'Microsoft.Web/sites/config@2023-01-01' = {
  name: '${externalFunctionAppName}/appsettings'
  dependsOn: [
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
