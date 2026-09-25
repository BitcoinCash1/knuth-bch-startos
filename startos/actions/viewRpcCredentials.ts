import { sdk } from '../sdk'
import { storeJson } from '../fileModels/store.json'
import { networkPorts, Network } from '../utils'
import { i18n } from '../i18n'

export const viewRpcCredentials = sdk.Action.withoutInput(
  'rpc-credentials',

  async ({ effects: _effects }) => ({
    name: i18n('RPC Credentials'),
    description:
      i18n('View the JSON-RPC username, password and port for connecting mining pools and other services.'),
    warning: null,
    allowedStatuses: 'any' as const,
    group: i18n('Credentials'),
    visibility: 'enabled' as const,
  }),

  async ({ effects }) => {
    const store = await storeJson.read().once()
    const network: Network = store?.network ?? 'mainnet'
    const { rpc: rpcPort } = networkPorts[network]

    if (!store?.rpcEnabled) {
      return {
        version: '1' as const,
        title: i18n('RPC Credentials'),
        message:
          i18n('The JSON-RPC server is currently disabled. Enable it under Node Settings → JSON-RPC Server.'),
        result: null,
      }
    }

    return {
      version: '1' as const,
      title: i18n('RPC Credentials'),
      message: i18n('JSON-RPC is listening on port ${rpcPort} (${network}).', { rpcPort: String(rpcPort), network }),
      result: {
        type: 'group' as const,
        name: i18n('JSON-RPC'),
        description: i18n('Credentials for connecting mining pools and other services'),
        value: [
          {
            type: 'single' as const,
            name: i18n('Username'),
            description: i18n('JSON-RPC username'),
            value: store?.rpcUser ?? '',
            copyable: true,
            qr: false,
            masked: false,
          },
          {
            type: 'single' as const,
            name: i18n('Password'),
            description: i18n('JSON-RPC password'),
            value: store?.rpcPassword ?? '',
            copyable: true,
            qr: false,
            masked: true,
          },
          {
            type: 'single' as const,
            name: i18n('Port'),
            description: i18n('JSON-RPC port for this network'),
            value: String(rpcPort),
            copyable: true,
            qr: false,
            masked: false,
          },
        ],
      },
    }
  },
)
