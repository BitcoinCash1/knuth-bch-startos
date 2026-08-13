import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_0_6 = VersionInfo.of({
  version: '1.3.0:6',
  releaseNotes:
    'Packaging revision of kth 1.3.0 (upstream version is unchanged). ' +
    'Health checks now match BCHN/BCHD/Flowee. A Bitcoin-RPC compatibility ' +
    'sidecar serves getblock/getrawtransaction/getblocktemplate from blk*.dat ' +
    'so Fulcrum, Explorer, and the pools can use Knuth. Blockchain Sync prefers ' +
    'the sidecar tip so a few-block native blocks/headers lag is Synced, not ' +
    '"Syncing 100%". Tor stays optional.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
