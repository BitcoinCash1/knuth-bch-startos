import { sdk } from '../sdk'
import { mainMounts } from '../mounts'
import { networkHostsFile, Network, rootDir } from '../utils'
import { storeJson } from '../file-models/store.json'
import { knuthConf } from '../file-models/knuth.conf'

export const deletePeerList = sdk.Action.withoutInput(
  'delete-peer-list',
  async ({ effects: _effects }) => ({
    name: 'Delete Peer List',
    description:
      'Delete the peer hosts file to reset the address database. The node will rebuild it from DNS seeds on next startup.',
    warning:
      'All known peer addresses will be lost. The node will need to rediscover peers on next startup, which may take a few minutes.',
    allowedStatuses: 'only-stopped' as const,
    group: 'Maintenance',
    visibility: 'enabled' as const,
  }),
  async ({ effects }) => {
    const store = await storeJson.read().once()
    const network: Network = store?.network ?? 'mainnet'
    const conf = await knuthConf.read().once()
    // Prefer the path kth is actually configured to use; fall back to the
    // per-network default. Also sweep legacy locations from earlier packages.
    const hostsFile =
      conf?.['net.hosts_file'] || networkHostsFile(network)
    const legacy = [
      `${rootDir}/peers.dat`,
      `${rootDir}/blockchain/peers.dat`,
      '/peers.dat',
    ]

    await sdk.SubContainer.withTemp(
      effects,
      { imageId: 'knuth' },
      mainMounts,
      'delete-peer-list',
      async (sub) => {
        // Per-network peers.dat (chipnet/testnet/…) plus every legacy path.
        // kth keeps bans in this file — leaving any copy makes seeds stay banned.
        await sub.exec([
          'sh',
          '-c',
          `rm -f ${[hostsFile, ...legacy].join(' ')} ${rootDir}/*/peers.dat`,
        ])
      },
    )
    return {
      version: '1' as const,
      title: 'Peer List Deleted',
      message: `Peer hosts file removed (${hostsFile}). The node will rebuild it from DNS seeds on next startup.`,
      result: null,
    }
  },
)
