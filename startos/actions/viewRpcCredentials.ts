import { sdk } from '../sdk'
import { storeJson } from '../file-models/store.json'
import { networkPorts, Network } from '../utils'

export const viewRpcCredentials = sdk.Action.withoutInput(
  'rpc-credentials',

  async ({ effects: _effects }) => ({
    name: 'RPC Credentials',
    description:
      'View the JSON-RPC username, password and port for connecting mining pools and other services.',
    warning: null,
    allowedStatuses: 'any' as const,
    group: 'Credentials',
    visibility: 'enabled' as const,
  }),

  async ({ effects }) => {
    const store = await storeJson.read().once()
    const network: Network = store?.network ?? 'mainnet'
    const { rpc: rpcPort } = networkPorts[network]

    if (!store?.rpcEnabled) {
      return {
        version: '1' as const,
        title: 'RPC Credentials',
        message:
          'The JSON-RPC server is currently disabled. Enable it under Node Settings → JSON-RPC Server.',
        result: null,
      }
    }

    return {
      version: '1' as const,
      title: 'RPC Credentials',
      message: `JSON-RPC is listening on port ${rpcPort} (${network}).`,
      result: {
        type: 'group' as const,
        name: 'JSON-RPC',
        description: 'Credentials for connecting mining pools and other services',
        value: [
          {
            type: 'single' as const,
            name: 'Username',
            description: 'JSON-RPC username',
            value: store?.rpcUser ?? '',
            copyable: true,
            qr: false,
            masked: false,
          },
          {
            type: 'single' as const,
            name: 'Password',
            description: 'JSON-RPC password',
            value: store?.rpcPassword ?? '',
            copyable: true,
            qr: false,
            masked: true,
          },
          {
            type: 'single' as const,
            name: 'Port',
            description: 'JSON-RPC port for this network',
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
