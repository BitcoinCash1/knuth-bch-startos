import { sdk } from '../sdk'
import { mainMounts } from '../mounts'
import { networkHostsFile, Network, rootDir } from '../utils'
import { storeJson } from '../fileModels/store.json'
import { knuthConf } from '../fileModels/knuth.conf'
import { i18n } from '../i18n'

export const deletePeerList = sdk.Action.withoutInput(
  'delete-peer-list',
  async ({ effects: _effects }) => ({
    name: i18n('Delete Peer List'),
    description:
      i18n('Delete the peer hosts file to reset the address database. The node will rebuild it from DNS seeds on next startup.'),
    warning:
      i18n('All known peer addresses will be lost. The node will need to rediscover peers on next startup, which may take a few minutes.'),
    allowedStatuses: 'only-stopped' as const,
    group: i18n('Maintenance'),
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
      title: i18n('Peer List Deleted'),
      message: i18n('Peer hosts file removed (${hostsFile}). The node will rebuild it from DNS seeds on next startup.', { hostsFile }),
      result: null,
    }
  },
)
