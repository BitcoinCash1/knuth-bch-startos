import { sdk } from './sdk'
import { storeJson } from './file-models/store.json'

// Tor is optional in the manifest (same as BCHN/BCHD/Flowee). It is only a
// running requirement when the operator turns Tor routing on in Node Settings.
export const setDependencies = sdk.setupDependencies(async ({ effects }) => {
  const store = await storeJson.read().const(effects)
  const torEnabled = store?.torEnabled ?? false

  if (!torEnabled) return {}

  return {
    tor: {
      kind: 'running' as const,
      versionRange: '>=0.4.9.5:0',
      healthChecks: [] as string[],
    },
  }
})
