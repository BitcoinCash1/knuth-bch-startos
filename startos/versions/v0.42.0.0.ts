import { VersionInfo } from '@start9labs/start-sdk'

export const v_0_42_0_0 = VersionInfo.of({
  version: '0.42.0:0',
  releaseNotes:
    'Initial release of Knuth for StartOS. High-performance C++ Bitcoin Cash full node with Bitcoin Core-compatible JSON-RPC, ZMQ notifications, IPC via C-API, and UTXO queries.',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
