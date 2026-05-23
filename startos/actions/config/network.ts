import { sdk } from '../../sdk'
import { storeJson } from '../../file-models/store.json'
import { Network } from '../../utils'

const { InputSpec, Value } = sdk

const inputSpec = InputSpec.of({
  network: Value.select({
    name: 'Network',
    description: 'The Bitcoin Cash network to connect to.',
    warning: null,
    default: 'mainnet',
    values: {
      mainnet:  { name: 'Mainnet',  description: 'Main Bitcoin Cash network (default).' },
      testnet3: { name: 'Testnet3', description: 'Bitcoin Cash testnet3.' },
      testnet4: { name: 'Testnet4', description: 'Bitcoin Cash testnet4.' },
      scalenet: { name: 'Scalenet', description: 'Bitcoin Cash scalenet (big blocks).' },
      chipnet:  { name: 'Chipnet',  description: 'Bitcoin Cash chipnet (CashTokens chip testing).' },
      regtest:  { name: 'Regtest',  description: 'Local regression testing network.' },
    },
  }),
})

export const networkConfig = sdk.Action.withInput(
  'network-config',
  async ({ effects: _effects }) => ({
    name: 'Network',
    description:
      'Select the Bitcoin Cash network. The P2P port adjusts automatically for the selected network.',
    warning:
      'Changing the network requires a restart and will switch to a separate data directory.',
    allowedStatuses: 'any' as const,
    group: 'Configuration',
    visibility: 'enabled' as const,
  }),
  inputSpec,
  async ({ effects: _effects }) => {
    const store = await storeJson.read().once()
    return { network: (store?.network ?? 'mainnet') as Network }
  },
  async ({ effects, input }) => {
    await storeJson.merge(effects, { network: input.network as Network })
    await effects.restart()
    return null
  },
)
