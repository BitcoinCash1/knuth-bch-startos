import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_0_3 = VersionInfo.of({
  version: '1.3.0:3',
  releaseNotes:
    'Autoconfig now applies databaseMode and rpcEnabled (so Fulcrum/Explorer can ' +
    'turn RPC on and require full DB mode). Network action wording matches BCHN ' +
    '(RPC + P2P ports). Tor remains optional; enabling it requires the Tor package. ' +
    'Upstream RPC method gaps for Fulcrum/pools: k-nuth/kth#616.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
