import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

export const current = VersionInfo.of({
  version: '1.4.0:0',
  releaseNotes:
    'Upstream Knuth 1.4.0, built with the official RPC-enabled Docker recipe. ' +
    'The UTXO-Z database format is incompatible with older releases: stop ' +
    'Knuth and rebuild its blockchain database before starting this version. ' +
    'RPC credentials and network settings can be retained. Sync health and ' +
    'the compatibility RPC now use the native validated chain tip. ARM64 and ' +
    'RISC-V packages still emulate the x86_64 image.',
  migrations: {
    up: async () => {},
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
