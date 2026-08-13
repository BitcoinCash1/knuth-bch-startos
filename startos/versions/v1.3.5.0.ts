import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_5_0 = VersionInfo.of({
  version: '1.3.5:0',
  releaseNotes:
    'Serve a Bitcoin-RPC compatibility sidecar in front of kth. Knuth v1.3.0 ' +
    'documents getblock/getrawtransaction but fetch_block() still returns ' +
    'not_found after the move to blk*.dat, and there is no getnetworkinfo or ' +
    'classic getblocktemplate. Fulcrum, BCH Explorer, ASICSeer and EloPool now ' +
    'talk to the sidecar, which reads blocks from the flat files and fills in ' +
    'the missing methods (including validateaddress and submitblock→submitblocklight).',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
