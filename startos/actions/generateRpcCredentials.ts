import { sdk } from '../sdk'
import { storeJson } from '../file-models/store.json'
import { knuthConf } from '../file-models/knuth.conf'

const randomHex = (bytes: number) =>
  Array.from({ length: bytes }, () =>
    Math.floor(Math.random() * 256)
      .toString(16)
      .padStart(2, '0'),
  ).join('')

// BCHN can hold many rpcauth entries; kth v1.3.0 has a single rpc.user /
// rpc.password pair, so this rotates that one credential rather than adding
// another. Existing clients must be updated with the new password.
export const generateRpcCredentials = sdk.Action.withoutInput(
  'generate-rpc-credentials',

  async ({ effects: _effects }) => ({
    name: 'Generate RPC Credentials',
    description:
      'Generate a new RPC password for external services (wallet, indexer, miner). Knuth supports a single RPC credential, so this replaces the existing one.',
    warning:
      'This replaces the current RPC password. Every service configured with the old password will fail to authenticate until updated.',
    allowedStatuses: 'any' as const,
    group: 'Credentials',
    visibility: 'enabled' as const,
  }),

  async ({ effects }) => {
    const store = await storeJson.read().once()
    const user = store?.rpcUser || 'knuth'
    const password = randomHex(16)

    await storeJson.merge(effects, { rpcUser: user, rpcPassword: password })
    await knuthConf.merge(effects, {
      'rpc.user': user,
      'rpc.password': password,
    })
    await effects.restart()

    return {
      version: '1' as const,
      title: 'RPC Credentials Generated',
      message:
        'A new RPC password has been generated and the node is restarting. Save it now — you can view it again from the RPC Credentials action.',
      result: {
        type: 'group' as const,
        name: 'New JSON-RPC Credential',
        description: 'Update dependent services with these values',
        value: [
          {
            type: 'single' as const,
            name: 'Username',
            description: 'JSON-RPC username',
            value: user,
            copyable: true,
            qr: false,
            masked: false,
          },
          {
            type: 'single' as const,
            name: 'Password',
            description: 'JSON-RPC password',
            value: password,
            copyable: true,
            qr: false,
            masked: true,
          },
        ],
      },
    }
  },
)
