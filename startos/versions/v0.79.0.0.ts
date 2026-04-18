import { VersionInfo } from '@start9labs/start-sdk'

export const v_0_79_0_0 = VersionInfo.of({
  version: '0.79.0:0',
  releaseNotes:
    'Align package with Knuth v0.79.0 and StartOS action patterns. Adds Node Settings grouping, hidden autoconfig wiring, IPC/UTXOZ capability flags enabled by default, and architecture metadata for x86_64/aarch64/riscv64. RPC and gRPC remain unsupported in this package version.',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
