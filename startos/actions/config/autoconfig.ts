import { knuthConf, fullConfigSpec } from '../../file-models/knuth.conf'
import { storeJson } from '../../file-models/store.json'
import { sdk } from '../../sdk'

// Hidden cross-package action (Fulcrum / Explorer / pools). Mirrors BCHN
// autoconfig: apply only the fields a dependent asks for, leave the rest alone.
export const autoconfig = sdk.Action.withInput(
  'autoconfig',

  async ({ effects: _effects }) => ({
    name: 'Auto-Configure',
    description:
      'Automatically configure Knuth for the needs of another service',
    warning: null,
    allowedStatuses: 'any' as const,
    group: null,
    visibility: 'hidden' as const,
  }),

  async ({ effects: _effects, prefill }) => {
    if (!prefill) return fullConfigSpec

    return fullConfigSpec
      .filterFromPartial(prefill as typeof fullConfigSpec._PARTIAL)
      .disableFromPartial(
        prefill as typeof fullConfigSpec._PARTIAL,
        'These fields were provided by a task and cannot be edited',
      )
  },

  async ({ effects: _effects }) => {
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
      dbMaxSize: dbMode === 'pruned' ? Math.round((rawMaxSize as number) / 1e9) : null,
      rpcEnabled: store?.rpcEnabled ?? false,
      ipcEnabled: store?.ipcEnabled ?? true,
      utxozEnabled: store?.utxozEnabled ?? true,
      torEnabled: store?.torEnabled ?? false,
    }
  },

  async ({ effects, input }) => {
    const store = await storeJson.read().once()
    const confPatch: Record<string, unknown> = {}

    if (input.verboseLogging !== undefined)
      confPatch['log.verbose'] = input.verboseLogging
    if (input.outboundConnections !== undefined)
      confPatch['net.outbound_connections'] = input.outboundConnections
    if (input.inboundConnections !== undefined)
      confPatch['net.inbound_connections'] = input.inboundConnections
    if (input.blockLatencySeconds !== undefined)
      confPatch['node.block_latency_seconds'] = input.blockLatencySeconds

    if (input.databaseMode !== undefined) {
      const dbMode = input.databaseMode as 'full' | 'blocks' | 'pruned'
      confPatch['db.db_mode'] = dbMode
      if (dbMode === 'pruned' && input.dbMaxSize) {
        confPatch['db.db_max_size'] = (input.dbMaxSize as number) * 1e9
      }
    }

    const rpcEnabled =
      input.rpcEnabled !== undefined
        ? !!input.rpcEnabled
        : (store?.rpcEnabled ?? false)
    if (input.rpcEnabled !== undefined) {
      confPatch['rpc.enabled'] = rpcEnabled
      confPatch['rpc.user'] = store?.rpcUser ?? ''
      confPatch['rpc.password'] = store?.rpcPassword ?? ''
    }

    if (Object.keys(confPatch).length > 0) {
      await knuthConf.merge(effects, confPatch as never)
    }

    const storePatch: Record<string, unknown> = {}
    if (input.rpcEnabled !== undefined) storePatch.rpcEnabled = rpcEnabled
    if (input.ipcEnabled !== undefined) storePatch.ipcEnabled = input.ipcEnabled
    if (input.utxozEnabled !== undefined)
      storePatch.utxozEnabled = input.utxozEnabled
    if (input.torEnabled !== undefined) storePatch.torEnabled = input.torEnabled

    if (Object.keys(storePatch).length > 0) {
      await storeJson.merge(effects, storePatch as never)
    }

    // store.json is read .once() in main — restart so rpc/tor toggles apply.
    if (Object.keys(storePatch).length > 0 || confPatch['rpc.enabled'] !== undefined) {
      await effects.restart()
    }
  },
)
