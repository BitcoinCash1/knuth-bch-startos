import { sdk } from '../sdk'
import { mainMounts } from '../mounts'
import { rootDir } from '../utils'

export const deletePeerList = sdk.Action.withoutInput(
  'delete-peer-list',
  async ({ effects: _effects }) => ({
    name: 'Delete Peer List',
    description:
      'Delete peers.dat to reset the peer address database. The node will rebuild it from DNS seeds on next startup.',
    warning:
      'All known peer addresses will be lost. The node will need to rediscover peers on next startup, which may take a few minutes.',
    allowedStatuses: 'only-stopped' as const,
    group: 'Maintenance',
    visibility: 'enabled' as const,
  }),
  async ({ effects }) => {
    await sdk.SubContainer.withTemp(
      effects,
      { imageId: 'knuth' },
      mainMounts,
      'delete-peer-list',
      async (sub) => {
        await sub.exec(['rm', '-f', `${rootDir}/peers.dat`])
      },
    )
    return {
      version: '1' as const,
      title: 'Peer List Deleted',
      message: 'peers.dat has been removed. The node will rebuild it from DNS seeds on next startup.',
      result: null,
    }
  },
)
