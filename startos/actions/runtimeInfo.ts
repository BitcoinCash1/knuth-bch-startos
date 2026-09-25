import { sdk } from '../sdk'
import { mainMounts } from '../mounts'
import { rootDir, Network, networkPorts } from '../utils'
import { storeJson } from '../fileModels/store.json'
import { i18n } from '../i18n'

export const runtimeInfo = sdk.Action.withoutInput(
  'runtime-info',
  async ({ effects: _effects }) => ({
    name: i18n('Node Info'),
    description: i18n('Display current Knuth node runtime information: network, sync status, database mode.'),
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

        lines.push(i18n('Network: ${network}', { network }))
        lines.push(i18n('P2P Port: ${port}', { port: String(peerPort) }))

        // Check blockchain directory exists
        const chainRes = await sub.exec(['test', '-d', `${rootDir}/blockchain`])
        lines.push(i18n('Blockchain data: ${status}', { status: chainRes.exitCode === 0 ? i18n('present') : i18n('not initialized') }))

        // Count block files as a sync proxy
        const lsRes = await sub.exec(['sh', '-c', `ls ${rootDir}/blockchain/blocks/ 2>/dev/null | wc -l`]).catch(() => null)
        if (lsRes?.exitCode === 0) {
          const count = lsRes.stdout.toString().trim()
          lines.push(i18n('Block segments: ${count}', { count }))
        }

        return {
          version: '1' as const,
          title: i18n('Knuth Node Info'),
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
