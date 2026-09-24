import { sdk } from './sdk'
import { storeJson } from './file-models/store.json'

// Tor is optional in the manifest (same as BCHN/BCHD/Flowee). StartOS only
// lists dependencies that setupDependencies returns — returning {} hid the
// Tor card on the VM even though the manifest marked it optional. Always
// advertise it: exists (installed, not required to be running) until the
// operator turns Tor Routing on, then require it running.
export const setDependencies = sdk.setupDependencies(async ({ effects }) => {
  const store = await storeJson.read().const(effects)
  const torEnabled = store?.torEnabled ?? false

  if (!torEnabled) {
    return {
      tor: {
        kind: 'exists' as const,
        versionRange: '>=0.4.9.5:0',
      },
    }
  }

  return {
    tor: {
      kind: 'running' as const,
      versionRange: '>=0.4.9.5:0',
      healthChecks: [] as string[],
    },
  }
})
