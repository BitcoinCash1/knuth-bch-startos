import { sdk } from '../../sdk'
import { knuthConf } from '../../file-models/knuth.conf'

const { InputSpec, Value } = sdk

// Mirrors BCHN's "Mempool & Block Policy" group, mapped onto the equivalent kth
// v1.3.0 flags. kth has no ancestor/descendant limits or mempool expiry, so this
// exposes the relay/fee policy and reorg pool knobs it does have.
const mempoolPolicySpec = InputSpec.of({
  byteFeeSatoshis: Value.number({
    name: 'Minimum Relay Fee',
    description:
      'Minimum fee in satoshis per byte required for a transaction to be relayed and mined.',
    warning: null,
    required: true,
    default: 1,
    min: 0,
    max: 1000,
    integer: false,
    units: 'sat/byte',
  }),
  sigopFeeSatoshis: Value.number({
    name: 'Sigop Fee',
    description:
      'Fee in satoshis charged per signature operation when evaluating transaction cost.',
    warning: null,
    required: true,
    default: 100,
    min: 0,
    max: 100000,
    integer: false,
    units: 'satoshis',
  }),
  minimumOutputSatoshis: Value.number({
    name: 'Dust Threshold',
    description:
      'Outputs below this value are treated as dust and will not be relayed.',
    warning: null,
    required: true,
    default: 546,
    min: 0,
    max: 100000,
    integer: true,
    units: 'satoshis',
  }),
  relayTransactions: Value.toggle({
    name: 'Relay Transactions',
    description:
      'Relay unconfirmed transactions to peers. Disable to run as a blocks-only node.',
    default: true,
  }),
  refreshTransactions: Value.toggle({
    name: 'Refresh Transactions',
    description: 'Re-announce unconfirmed transactions that peers have not seen.',
    default: true,
  }),
  reorgPoolLimit: Value.number({
    name: 'Reorg Pool Limit',
    description:
      'Number of blocks worth of transactions kept available for re-insertion after a chain reorganization.',
    warning: null,
    required: true,
    default: 100,
    min: 0,
    max: 10000,
    integer: true,
    units: 'blocks',
  }),
  reorganizationLimit: Value.number({
    name: 'Maximum Reorganization Depth',
    description:
      'Deepest chain reorganization the node will accept. 0 disables the limit.',
    warning: null,
    required: true,
    default: 256,
    min: 0,
    max: 10000,
    integer: true,
    units: 'blocks',
  }),
})

export const mempoolPolicy = sdk.Action.withInput(
  'mempool-policy',

  async ({ effects: _effects }) => ({
    name: 'Mempool & Block Policy',
    description:
      'Configure relay fees, dust threshold, transaction relay, and reorganization limits.',
    warning: null,
    allowedStatuses: 'any',
    group: 'Configuration',
    visibility: 'enabled',
  }),

  mempoolPolicySpec,

  async ({ effects }) => {
    const conf = await knuthConf.read().once()
    return {
      byteFeeSatoshis: conf?.['node.byte_fee_satoshis'] ?? 1,
      sigopFeeSatoshis: conf?.['node.sigop_fee_satoshis'] ?? 100,
      minimumOutputSatoshis: conf?.['node.minimum_output_satoshis'] ?? 546,
      relayTransactions: conf?.['node.relay_transactions'] ?? true,
      refreshTransactions: conf?.['node.refresh_transactions'] ?? true,
      reorgPoolLimit: conf?.['db.reorg_pool_limit'] ?? 100,
      reorganizationLimit: conf?.['chain.reorganization_limit'] ?? 256,
    }
  },

  async ({ effects, input }) => {
    await knuthConf.merge(effects, {
      'node.byte_fee_satoshis': input.byteFeeSatoshis,
      'node.sigop_fee_satoshis': input.sigopFeeSatoshis,
      'node.minimum_output_satoshis': input.minimumOutputSatoshis,
      'node.relay_transactions': input.relayTransactions,
      'node.refresh_transactions': input.refreshTransactions,
      'db.reorg_pool_limit': input.reorgPoolLimit,
      'chain.reorganization_limit': input.reorganizationLimit,
    })
    await effects.restart()
    return null
  },
)
