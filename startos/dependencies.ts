import { sdk } from './sdk'
import { storeJson } from './fileModels/store.json'
import { torDescription } from './manifest/i18n'

const tor = sdk.Dependency.optional('tor', {
  description: torDescription,
  metadata: {
    title: 'Tor',
    icon: 'https://raw.githubusercontent.com/Start9Labs/tor-startos/65faea17febc739d910e8c26ff4e61f6333487a8/icon.svg',
  },
  versionRange: '>=0.4.9.5:0',
  kind: 'running',
  healthChecks: [],
  enabled: async ({ effects }) =>
    (await storeJson.read((store) => store.torEnabled).const(effects)) ?? false,
})

export const dependencies = sdk.Dependencies.of().addDependency(tor)
