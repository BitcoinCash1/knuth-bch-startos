import { sdk } from '../sdk'
import { mainMounts } from '../mounts'
import { rootDir, Network, networkPorts } from '../utils'
import { storeJson } from '../file-models/store.json'

export const runtimeInfo = sdk.Action.withoutInput(
  'runtime-info',
  async ({ effects: _effects }) => ({
    name: 'Node Info',
    description: 'Display current Knuth node runtime information: network, sync status, database mode.',
    warning: null,
    allowedStatuses: 'only-running' as const,
    group: null,
    visibility: 'enabled' as const,
  }),
  async ({ effects }) => {
    const store = await storeJson.read().once()
    const network: Network = store?.network ?? 'mainnet'
    const { peer: peerPort } = networkPorts[network]

    return sdk.SubContainer.withTemp(
      effects,
      { imageId: 'knuth' },
      mainMounts,
      'runtime-info',
      async (sub) => {
        const lines: string[] = []

        lines.push(`Network: ${network}`)
        lines.push(`P2P Port: ${peerPort}`)

        // Check blockchain directory exists
        const chainRes = await sub.exec(['test', '-d', `${rootDir}/blockchain`])
        lines.push(`Blockchain data: ${chainRes.exitCode === 0 ? 'present' : 'not initialized'}`)

        // Count block files as a sync proxy
        const lsRes = await sub.exec(['sh', '-c', `ls ${rootDir}/blockchain/blocks/ 2>/dev/null | wc -l`]).catch(() => null)
        if (lsRes?.exitCode === 0) {
          const count = lsRes.stdout.toString().trim()
          lines.push(`Block segments: ${count}`)
        }

        return {
          version: '1' as const,
          title: 'Knuth Node Info',
          message: null,
          result: {
            type: 'single' as const,
            value: lines.join('\n'),
            copyable: false,
            qr: false,
            masked: false,
          },
        }
      },
    )
  },
)
