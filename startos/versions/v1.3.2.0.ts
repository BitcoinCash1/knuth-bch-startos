import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_2_0 = VersionInfo.of({
  version: '1.3.2:0',
  releaseNotes:
    'Stop actually stops (45s then SIGKILL) so Delete Peer List and Delete ' +
    'Test Network Data work. You can wipe the active test network (e.g. chipnet) ' +
    'after stopping. Blockchain Sync uses Knuth\'s own "Fully synced at height" ' +
    'log because getblockchaininfo.blocks stays 0 even at the tip.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
