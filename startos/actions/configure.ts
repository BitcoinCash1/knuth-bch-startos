import { sdk } from '../sdk'
import { knuthConf, fullConfigSpec } from '../file-models/knuth.conf'
import { storeJson } from '../file-models/store.json'

export const configure = sdk.Action.withInput(
  'node-settings',

  async ({ effects }) => ({
    name: 'Node Settings',
    description: 'Core node behavior, database mode, IPC capabilities, and UTXOZ support.',
    warning: null,
    allowedStatuses: 'any',
    group: 'Configuration',
    visibility: 'enabled',
  }),

  fullConfigSpec,

  async ({ effects }) => {
    const conf = await knuthConf.read().once()
    const store = await storeJson.read().once()
    const dbMode = conf?.['database.db_mode'] ?? 'full_indexed'
    const rawMaxSize = conf?.['database.db_max_size'] ?? 600000000000
    return {
      verboseLogging: conf?.['log.verbose'] ?? false,
      outboundConnections: conf?.['network.outbound_connections'] ?? 8,
      inboundConnections: conf?.['network.inbound_connections'] ?? 32,
      blockLatencySeconds: conf?.['node.block_latency_seconds'] ?? 60,
      databaseMode: dbMode,
      // dbMaxSize stored as bytes in kth.cfg; show as GB in UI only when pruned
      dbMaxSize: dbMode === 'pruned' ? Math.round((rawMaxSize as number) / 1e9) : null,
      ipcEnabled: store?.ipcEnabled ?? true,
      utxozEnabled: store?.utxozEnabled ?? true,
      torEnabled: store?.torEnabled ?? false,
    }
  },

  async ({ effects, input }) => {
    const dbMode = input.databaseMode ?? 'full_indexed'
    const dbMaxSizeGb = input.dbMaxSize
    const dbMaxSizeBytes =
      dbMode === 'pruned' && dbMaxSizeGb
        ? (dbMaxSizeGb as number) * 1e9
        : 600000000000

    await knuthConf.merge(effects, {
      'log.verbose': input.verboseLogging,
      'network.outbound_connections': input.outboundConnections,
      'network.inbound_connections': input.inboundConnections,
      // Always-on settings — not exposed in UI
      'node.compact_blocks_high_bandwidth': true,
      'node.ds_proofs_enabled': true,
      'node.ds_proofs': true,
      'network.relay_transactions': true,
      'node.block_latency_seconds': input.blockLatencySeconds,
      'database.db_mode': dbMode,
      'database.db_max_size': dbMaxSizeBytes,
    })

    await storeJson.merge(effects, {
      ipcEnabled: input.ipcEnabled ?? true,
      utxozEnabled: input.utxozEnabled ?? true,
      torEnabled: input.torEnabled ?? false,
    })

    return null
  },
)
