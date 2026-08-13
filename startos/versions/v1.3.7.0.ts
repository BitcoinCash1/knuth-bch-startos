import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_7_0 = VersionInfo.of({
  version: '1.3.7:0',
  releaseNotes:
    'RPC sidecar: incremental blk*.dat index (no 10s full rescan per new block), ' +
    'bitcoind-style help() so Fulcrum proceeds past handshake, keep-alive leftover ' +
    'buffer so pipelined uptime is not dropped, real GBT bits from the previous header.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
