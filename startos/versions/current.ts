import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

export const current = VersionInfo.of({
  version: '1.5.2:1',
  releaseNotes:
    'Add Tempnet as a network option. Tempnet runs kth commit ' +
    '880af5356b0a22a78439dea9c1a25c5a16f93cf7 from CyberAshven/kth, with ' +
    'Tempnet support (#752) and the fast-sync crash fix (#815) pending an upstream release. ' +
    'Every other network is unchanged and continues to run released kth 1.5.2.',
  migrations: {
    up: async () => {},
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
