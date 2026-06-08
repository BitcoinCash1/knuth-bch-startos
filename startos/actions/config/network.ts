import { sdk } from '../../sdk'
import { storeJson } from '../../file-models/store.json'
import { Network } from '../../utils'

const { InputSpec, Value } = sdk

const networkSpec = InputSpec.of({
  network: Value.select({
    name: 'Network',
    description:
      'Bitcoin Cash network to connect to. Changing this requires a node restart and a separate data directory per network.',
    warning:
      'Switching networks requires a full restart. The node will sync from scratch on the new network. Your mainnet data is preserved separately on disk.',
    values: {
      mainnet:  'Mainnet',
      testnet3: 'Testnet3 (legacy test network)',
      testnet4: 'Testnet4 (light-weight test network)',
      scalenet: 'Scalenet (high-throughput test network)',
      chipnet:  'Chipnet (upgrade / CHIP staging)',
      regtest:  'Regtest (local testing only)',
    },
    default: 'mainnet',
  }),
})

export const networkConfig = sdk.Action.withInput(
  'network-config',
  async ({ effects: _effects }) => ({
    name: 'Network',
    description:
      'Select the Bitcoin Cash network. The P2P port adjusts automatically for the selected network.',
    warning:
      'Changing the network requires a node restart. The P2P port will change to match the selected network.',
    allowedStatuses: 'any' as const,
    group: 'Configuration',
    visibility: 'enabled' as const,
  }),
  networkSpec,
  async ({ effects: _effects }) => {
    const store = await storeJson.read().once()
    return { network: (store?.network ?? 'mainnet') as Network }
  },
  async ({ effects, input }) => {
    const store = await storeJson.read().once()
    const current = store?.network ?? 'mainnet'
    const next = input.network as Network
    if (current === next) {
      return {
        version: '1' as const,
        title: 'Network Unchanged',
        message: `Knuth is already configured for ${next}.`,
        result: null,
      }
    }
    await storeJson.merge(effects, { network: next })
    await effects.restart()
    return {
      version: '1' as const,
      title: 'Network Updated',
      message: `Switched Knuth from ${current} to ${next}. Restarting automatically.`,
      result: null,
    }
  },
)
