import { i18n } from '../i18n'
import { sdk } from '../sdk'
import { storeJson } from '../fileModels/store.json'
import { knuthConf } from '../fileModels/knuth.conf'
import { generateRpcPassword, rpcUser as defaultRpcUser } from '../utils'

// kth v1.3.0 has a single rpc.user / rpc.password pair rather than BCHN's
// multiple rpcauth entries, so this rotates the one credential.
export const generateRpcCredentials = sdk.Action.withoutInput(
  'generate-rpc-credentials',

  async ({ effects: _effects }) => ({
    name: i18n('Generate RPC Credentials'),
    description: i18n(
      'Generate a new JSON-RPC password for external services such as a wallet, indexer, or miner. Knuth supports one credential, so this replaces the existing one.',
    ),
    warning: i18n(
      'Every service configured with the old password will fail to authenticate until it is updated.',
    ),
    allowedStatuses: 'any' as const,
    group: 'Credentials',
    visibility: 'enabled' as const,
  }),

  async ({ effects }) => {
    const store = await storeJson.read().once()
    const user = store?.rpcUser || defaultRpcUser
    const password = generateRpcPassword()

    await storeJson.merge(effects, { rpcUser: user, rpcPassword: password })
    await knuthConf.merge(effects, {
      'rpc.user': user,
      'rpc.password': password,
    })
    await effects.restart()

    return {
      version: '1' as const,
      title: i18n('RPC Credentials Generated'),
      message: i18n(
        'A new JSON-RPC password has been generated and the node is restarting. You can view it again from the RPC Credentials action.',
      ),
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
            launchable: false,
          },
          {
            type: 'single' as const,
            name: i18n('Password'),
            description: i18n('JSON-RPC password'),
            value: password,
            copyable: true,
            qr: false,
            masked: true,
            launchable: false,
          },
        ],
      },
    }
  },
)
