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
    const dbMode = conf?.['db.db_mode'] ?? 'full'
    const rawMaxSize = conf?.['db.db_max_size'] ?? 600000000000
    return {
      verboseLogging: conf?.['log.verbose'] ?? false,
      outboundConnections: conf?.['net.outbound_connections'] ?? 8,
      inboundConnections: conf?.['net.inbound_connections'] ?? 32,
      blockLatencySeconds: conf?.['node.block_latency_seconds'] ?? 60,
      databaseMode: dbMode,
      // dbMaxSize stored as bytes in kth.cfg; show as GB in UI only when pruned
      dbMaxSize: dbMode === 'pruned' ? Math.round((rawMaxSize as number) / 1e9) : null,
      rpcEnabled: store?.rpcEnabled ?? false,
      ipcEnabled: store?.ipcEnabled ?? true,
      utxozEnabled: store?.utxozEnabled ?? true,
      torEnabled: store?.torEnabled ?? false,
    }
  },

  async ({ effects, input }) => {
    const dbMode = (input.databaseMode ?? 'full') as 'full' | 'blocks' | 'pruned'
    const dbMaxSizeGb = input.dbMaxSize
    const dbMaxSizeBytes =
      dbMode === 'pruned' && dbMaxSizeGb
        ? (dbMaxSizeGb as number) * 1e9
        : 600000000000

    const store = await storeJson.read().once()
    const rpcEnabled = input.rpcEnabled ?? false

    await knuthConf.merge(effects, {
      'log.verbose': input.verboseLogging,
      'net.outbound_connections': input.outboundConnections,
      'net.inbound_connections': input.inboundConnections,
      // Always-on settings — not exposed in UI
      'node.compact_blocks_high_bandwidth': true,
      'node.ds_proofs': true,
      'node.relay_transactions': true,
      'node.block_latency_seconds': input.blockLatencySeconds,
      'db.db_mode': dbMode,
      'db.db_max_size': dbMaxSizeBytes,
      'rpc.enabled': rpcEnabled,
      'rpc.user': store?.rpcUser ?? '',
      'rpc.password': store?.rpcPassword ?? '',
    })

    await storeJson.merge(effects, {
      rpcEnabled,
      ipcEnabled: input.ipcEnabled ?? true,
      utxozEnabled: input.utxozEnabled ?? true,
      torEnabled: input.torEnabled ?? false,
    })

    // rpcEnabled/ipcEnabled/utxozEnabled/torEnabled live in store.json, which
    // main reads with .once() — without an explicit restart the toggle would
    // save but never take effect. kth.cfg is watched via .const, but restarting
    // once here covers both.
    await effects.restart()

    return null
  },
)
