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

@description('Trading-session function app name.')
param tradingFunctionAppName string = 'func-rehoboam'

@description('External-data refresh function app name (REH-41 Phase 2).')
param externalFunctionAppName string = 'func-rehoboam-external'

@description('Key Vault name. Must be globally unique (3-24 chars, alphanumeric + hyphens).')
param keyVaultName string = 'kv-rehoboam-${uniqueString(resourceGroup().id)}'

@description('Kickbase login email.')
@secure()
param kickbaseEmail string

@description('Kickbase login password.')
@secure()
param kickbasePassword string

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
