import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_0_6 = VersionInfo.of({
  version: '1.3.0:6',
  releaseNotes:
    'Health checks now match BCHN/BCHD/Flowee: RPC, Blockchain Sync (with %), ' +
    'Peer Connections, Tor (optional), I2P, and Clearnet. Peer counts come from ' +
    "Knuth's own P2P status log because getpeerinfo is not implemented. Tor " +
    'stays an optional dependency and is only required when Tor routing is enabled.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
