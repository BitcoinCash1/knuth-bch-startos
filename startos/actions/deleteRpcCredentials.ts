import { sdk } from '../sdk'
import { storeJson } from '../file-models/store.json'
import { knuthConf } from '../file-models/knuth.conf'

// kth falls back to a generated .cookie file when rpc.user / rpc.password are
// empty, so clearing them does not lock the node out — it just revokes the
// shared password that dependent services were using.
export const deleteRpcCredentials = sdk.Action.withoutInput(
  'delete-rpc-credentials',

  async ({ effects: _effects }) => ({
    name: 'Delete RPC Credentials',
    description:
      'Clear the stored RPC username and password. Services using them will no longer be able to authenticate after the next restart.',
    warning:
      'Any wallet, indexer or miner configured with these credentials will stop working until new ones are generated.',
    allowedStatuses: 'any' as const,
    group: 'Credentials',
    visibility: 'enabled' as const,
  }),

  async ({ effects }) => {
    await storeJson.merge(effects, { rpcUser: '', rpcPassword: '' })
    await knuthConf.merge(effects, { 'rpc.user': '', 'rpc.password': '' })
    await effects.restart()

    return {
      version: '1' as const,
      title: 'RPC Credentials Deleted',
      message:
        'The RPC username and password have been cleared and the node is restarting. Knuth will fall back to a .cookie file until you generate new credentials.',
      result: null,
    }
  },
)
