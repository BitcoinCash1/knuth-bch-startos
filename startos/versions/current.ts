import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

export const current = VersionInfo.of({
  version: '1.5.0:0',
  releaseNotes:
    'Upstream Knuth 1.5.0, built from source with the merged upstream ' +
    'RPC-enabled Docker recipe. Coming from 1.3.x: the UTXO-Z database ' +
    'format changed in 1.4.0, so stop Knuth and run Rebuild Blockchain ' +
    'Database for each network before starting this version. RPC credentials ' +
    'and network settings are kept. Coming from 1.4.0: the database is reused ' +
    'as is. Upstream changes include per-block SigChecks limits, a separate ' +
    'Knuth standardness policy, block download resume and mempool fixes. ' +
    'ARM64 and RISC-V packages still emulate the x86_64 image.',
  migrations: {
    up: async () => {},
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
