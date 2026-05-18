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
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      alwaysOn: false
    }
  }
}

output name string = functionApp.name
output principalId string = functionApp.identity.principalId
output functionAppId string = functionApp.id
