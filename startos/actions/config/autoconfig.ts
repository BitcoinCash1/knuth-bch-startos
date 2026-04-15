import { knuthConf, fullConfigSpec } from '../../file-models/knuth.conf'
import { storeJson } from '../../file-models/store.json'
import { sdk } from '../../sdk'

export const autoconfig = sdk.Action.withInput(
  'autoconfig',

  async ({ effects }) => ({
    name: 'Auto-Configure',
    description:
      'Automatically configure Knuth for the needs of another service',
    warning: null,
    allowedStatuses: 'any',
    group: null,
    visibility: 'hidden',
  }),

  async ({ effects, prefill }) => {
    if (!prefill) return fullConfigSpec

    return fullConfigSpec
      .filterFromPartial(prefill as typeof fullConfigSpec._PARTIAL)
      .disableFromPartial(
        prefill as typeof fullConfigSpec._PARTIAL,
        'These fields were provided by a task and cannot be edited',
      )
  },

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
    const { torEnabled, ...rest } = input as any
    await knuthConf.merge(effects, {
      'log.verbose': rest.verboseLogging,
      'network.outbound_connections': rest.outboundConnections,
      'network.inbound_connections': rest.inboundConnections,
    })
    await storeJson.merge(effects, {
      torEnabled: torEnabled ?? false,
    })
  },
)
