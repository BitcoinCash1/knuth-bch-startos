import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

export const current = VersionInfo.of({
  version: '1.7.0:1',
  releaseNotes:
    'Upstream v1.7.0. Tempnet is removed: it ended on 15 November 2026, when ' +
    'its tests moved to chipnet.',
  migrations: {
    up: async () => {},
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
