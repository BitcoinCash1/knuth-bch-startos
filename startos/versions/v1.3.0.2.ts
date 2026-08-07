import { VersionInfo } from '@start9labs/start-sdk'
import { knuthConf } from '../file-models/knuth.conf'
import { storeJson } from '../file-models/store.json'
import {
  Network,
  networkDbDir,
  networkHostsFile,
  rootDir,
} from '../utils'
import { sdk } from '../sdk'
import { mainMounts } from '../mounts'

export const v_1_3_0_2 = VersionInfo.of({
  version: '1.3.0:2',
  releaseNotes:
    'Isolate chain and peer data per network (BCHN/BCHD/Flowee layout: mainnet ' +
    'under /data/blockchain, testnets under /data/<network>/), and pin kth\'s ' +
    'hosts file onto the StartOS volume (it was writing peers.dat to /). Fixes ' +
    'chipnet stuck at 0 peers after a mainnet→chipnet switch, and makes Delete ' +
    'Peer List / Delete Test Network Data hit the right paths.',
  migrations: {
    up: async ({ effects }) => {
      const store = await storeJson.read().once()
      const network: Network = store?.network ?? 'mainnet'
      const newDir = networkDbDir(network)
      const hostsFile = networkHostsFile(network)

      await knuthConf.merge(effects, {
        'db.directory': newDir,
        'net.hosts_file': hostsFile,
      })

      // Drop the root-cwd peers.dat (pre-fix location) and any flat
      // /data/peers.dat when the active network is a testnet so chipnet does
      // not inherit mainnet bans.
      await sdk.SubContainer.withTemp(
        effects,
        { imageId: 'knuth' },
        mainMounts,
        'migrate-network-datadir',
        async (sub) => {
          await sub.exec(['rm', '-f', '/peers.dat'])
          if (network !== 'mainnet') {
            // Leave /data/blockchain alone (mainnet data). Fresh testnet dir.
            await sub.exec(['rm', '-f', `${rootDir}/peers.dat`])
            // If a previous broken package put chipnet data in /data/blockchain
            // while store says chipnet, do not move it — start clean under
            // /data/<network>. Operator can delete mainnet via package reinstall.
          }
        },
      )
    },
    down: async ({ effects }) => {},
  },
})
