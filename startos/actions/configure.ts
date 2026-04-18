import { sdk } from '../sdk'
import { knuthConf, fullConfigSpec } from '../file-models/knuth.conf'
import { storeJson } from '../file-models/store.json'

export const configure = sdk.Action.withInput(
  'node-settings',

  async ({ effects }) => ({
    name: 'Node Settings',
    description: 'Core node behavior, IPC capabilities, and UTXOZ support.',
    warning: null,
    allowedStatuses: 'any',
    group: 'Configuration',
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
      compactBlocksHighBandwidth:
        conf?.['node.compact_blocks_high_bandwidth'] ?? true,
      dsProofsEnabled:
        conf?.['node.ds_proofs_enabled'] ?? conf?.['node.ds_proofs'] ?? true,
      relayTransactions: conf?.['network.relay_transactions'] ?? true,
      blockLatencySeconds: conf?.['node.block_latency_seconds'] ?? 60,
      ipcEnabled: store?.ipcEnabled ?? true,
      utxozEnabled: store?.utxozEnabled ?? true,
      torEnabled: store?.torEnabled ?? false,
    }
  },

  async ({ effects, input }) => {
    await knuthConf.merge(effects, {
      'log.verbose': input.verboseLogging,
      'network.outbound_connections': input.outboundConnections,
      'network.inbound_connections': input.inboundConnections,
      'node.compact_blocks_high_bandwidth': input.compactBlocksHighBandwidth,
      'node.ds_proofs_enabled': input.dsProofsEnabled,
      'node.ds_proofs': input.dsProofsEnabled,
      'network.relay_transactions': input.relayTransactions,
      'node.block_latency_seconds': input.blockLatencySeconds,
    })

    await storeJson.merge(effects, {
      ipcEnabled: input.ipcEnabled ?? true,
      utxozEnabled: input.utxozEnabled ?? true,
      torEnabled: input.torEnabled ?? false,
    })

    return null
  },
)
