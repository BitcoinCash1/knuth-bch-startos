import { sdk } from '../sdk'
import { storeJson } from '../fileModels/store.json'
import { knuthConf } from '../fileModels/knuth.conf'
import { i18n } from '../i18n'

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
    name: i18n('Generate RPC Credentials'),
    description:
      i18n('Generate a new RPC password for external services (wallet, indexer, miner). Knuth supports a single RPC credential, so this replaces the existing one.'),
    warning:
      i18n('This replaces the current RPC password. Every service configured with the old password will fail to authenticate until updated.'),
    allowedStatuses: 'any' as const,
    group: i18n('Credentials'),
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
      title: i18n('RPC Credentials Generated'),
      message:
        i18n('A new RPC password has been generated and the node is restarting. Save it now — you can view it again from the RPC Credentials action.'),
      result: {
        type: 'group' as const,
        name: i18n('New JSON-RPC Credential'),
        description: i18n('Update dependent services with these values'),
        value: [
          {
            type: 'single' as const,
            name: i18n('Username'),
            description: i18n('JSON-RPC username'),
            value: user,
            copyable: true,
            qr: false,
            masked: false,
          },
          {
            type: 'single' as const,
            name: i18n('Password'),
            description: i18n('JSON-RPC password'),
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
