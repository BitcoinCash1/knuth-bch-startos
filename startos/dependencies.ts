import { sdk } from './sdk'
import { storeJson } from './file-models/store.json'

export const setDependencies = sdk.setupDependencies(async ({ effects }) => {
  const store = await storeJson.read().const(effects)
  const torEnabled = store?.torEnabled ?? false

  if (torEnabled) {
    return {
      tor: {
        kind: 'running' as const,
        versionRange: '>=0.4.9.5:0',
        healthChecks: [] as string[],
      },
    }
  }

  return {}
})
