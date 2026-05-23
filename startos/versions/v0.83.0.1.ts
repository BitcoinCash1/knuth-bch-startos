import { VersionInfo } from '@start9labs/start-sdk'

export const v_0_83_0_1 = VersionInfo.of({
  version: '0.83.0:1',
  releaseNotes:
    'Add Network configuration (mainnet/testnet3/testnet4/scalenet/chipnet/regtest), ' +
    'Delete Peer List, Delete Test Network Data, and Node Info actions. ' +
    'Add database mode selection and pruning support to Node Settings. ' +
    'Remove redundant toggles for compact blocks, DS proofs, and relay (always enabled).',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
