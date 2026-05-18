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
