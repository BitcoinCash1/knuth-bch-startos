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

  async ({ effects }) => knuthConf.read().once(),

  async ({ effects, input }) => {
    const { torEnabled, zmqEnabled, txindex, prune, ...iniFields } = input as any
    await knuthConf.merge(effects, iniFields)
    await storeJson.merge(effects, {
      torEnabled: torEnabled ?? false,
    })
  },
)
