import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

export const current = VersionInfo.of({
  version: '1.7.0:1.2',
  releaseNotes:
    'TEST ONLY: package 1.7.0:1 with kth-jd built from CyberAshven/kth 7856e9b8 ' +
    '(the 8582780e of 1.7.0:1.1 merged with k-nuth/kth#1005, which brings #993: ' +
    'Job Declaration upstreams through Tor and I2P and a latency limit by route), ' +
    'reported as 1.7.0, and Node Settings -> Coinbase Tag.',
  migrations: {
    up: async () => {},
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
