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
      verboseLogging: conf?.['log.verbose'] ?? false,
      outboundConnections: conf?.['network.outbound_connections'] ?? 8,
      inboundConnections: conf?.['network.inbound_connections'] ?? 32,
      torEnabled: store?.torEnabled ?? false,
    }
  },

  async ({ effects, input }) => {
    await knuthConf.merge(effects, {
      'log.verbose': input.verboseLogging,
      'network.outbound_connections': input.outboundConnections,
      'network.inbound_connections': input.inboundConnections,
    })

    await storeJson.merge(effects, {
      torEnabled: input.torEnabled ?? false,
    })

    return null
  },
)
