import { sdk } from '../sdk'
import { knuthConf, fullConfigSpec } from '../file-models/knuth.conf'
import { storeJson } from '../file-models/store.json'

export const configure = sdk.Action.withInput(
  'configure',

  async ({ effects }) => ({
    name: 'Configure',
    description: 'Configure Knuth node settings',
    warning: null,
    allowedStatuses: 'any',
    group: null,
    visibility: 'enabled',
  }),

  fullConfigSpec,

  async ({ effects }) => {
    const conf = await knuthConf.read().once()
    const store = await storeJson.read().once()
    return {
      zmqEnabled: !!(conf?.zmqpubhashblock),
      txindex: true,
      prune: null,
      dbcache: conf?.dbcache ?? 512,
      maxconnections: conf?.maxconnections ?? 125,
      torEnabled: store?.torEnabled ?? false,
    }
  },

  async ({ effects, input }) => {
    const zmqFields = input.zmqEnabled
      ? {
          zmqpubhashblock: 'tcp://0.0.0.0:28332',
          zmqpubrawtx: 'tcp://0.0.0.0:28333',
          zmqpubhashtx: 'tcp://0.0.0.0:28334',
          zmqpubrawblock: 'tcp://0.0.0.0:28335',
        }
      : {
          zmqpubhashblock: '',
          zmqpubrawtx: '',
          zmqpubhashtx: '',
          zmqpubrawblock: '',
        }

    await knuthConf.merge(effects, {
      ...zmqFields,
      dbcache: input.dbcache,
      maxconnections: input.maxconnections,
    })

    await storeJson.merge(effects, {
      torEnabled: input.torEnabled ?? false,
    })

    return null
  },
)
