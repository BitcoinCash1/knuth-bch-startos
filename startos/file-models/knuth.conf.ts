import { FileHelper, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'

const iniNumber = z.union([z.string().transform(Number), z.number()])

// Key names follow kth v1.3.0 (k-nuth/kth#515 shortened them: network.* -> net.*,
// database.* -> db.*, blockchain.* -> chain.*, and relay_transactions moved to
// [node]). kth calls allow_config_extras(), so stale keys are silently ignored
// rather than rejected — a wrong name here fails quietly.
export const shape = z.object({
  // [log]
  'log.debug_file': z.string().catch('/data/debug.log'),
  'log.error_file': z.string().catch('/data/error.log'),
  'log.verbose': z.boolean().catch(false),

  // [net]
  'net.inbound_port': iniNumber.catch(8333),
  'net.inbound_connections': iniNumber.catch(32),
  'net.outbound_connections': iniNumber.catch(8),

  // [db]
  'db.directory': z.string().catch('/data/blockchain'),
  'db.db_max_size': iniNumber.catch(600000000000),
  'db.safe_mode': z.boolean().catch(true),
  'db.cache_capacity': iniNumber.catch(10000),
  'db.db_mode': z.enum(['full_indexed', 'normal', 'pruned']).catch('full_indexed'),

  // [chain]
  'chain.cores': iniNumber.catch(0),

  // [node]
  'node.compact_blocks_high_bandwidth': z.boolean().catch(true),
  'node.refresh_transactions': z.boolean().catch(true),
  'node.ds_proofs': z.boolean().catch(true),
  'node.relay_transactions': z.boolean().catch(true),
  'node.block_latency_seconds': iniNumber.catch(60),

  // [rpc] — added in kth v1.3.0. The server is also gated at compile time behind
  // the `rpc` conan option; rpc.enabled only works on a binary built with it.
  'rpc.enabled': z.boolean().catch(false),
  'rpc.bind': z.string().catch('0.0.0.0'),
  'rpc.port': iniNumber.catch(8332),
  'rpc.user': z.string().catch(''),
  'rpc.password': z.string().catch(''),
  'rpc.gbt_cache_size': iniNumber.catch(10),
  'rpc.gbt_store_time': iniNumber.catch(3600),
})

export const knuthConf = FileHelper.ini(
  {
    base: sdk.volumes.main,
    subpath: 'kth.cfg',
  },
  shape,
)

// Config spec for user-facing settings
export const fullConfigSpec = sdk.InputSpec.of({
  verboseLogging: sdk.Value.toggle({
    name: 'Verbose Logging',
    description: 'Enable verbose debug logging.',
    default: false,
  }),
  outboundConnections: sdk.Value.number({
    name: 'Outbound Connections',
    description: 'Target number of outbound peer connections.',
    required: true,
    default: 8,
    min: 0,
    max: 100,
    integer: true,
    units: null,
  }),
  inboundConnections: sdk.Value.number({
    name: 'Inbound Connections',
    description: 'Target number of inbound peer connections.',
    required: true,
    default: 32,
    min: 0,
    max: 1000,
    integer: true,
    units: null,
  }),
  blockLatencySeconds: sdk.Value.number({
    name: 'Block Latency Seconds',
    description: 'Block processing latency threshold used by the node.',
    required: true,
    default: 60,
    min: 1,
    max: 600,
    integer: true,
    units: 'seconds',
  }),
  databaseMode: sdk.Value.select({
    name: 'Database Mode',
    description:
      'Controls the indexing level of the Knuth blockchain database. ' +
      'Full Indexed is required for Fulcrum and BCH Explorer to work.',
    warning:
      'Switching from Full Indexed to Pruned will prevent Fulcrum and BCH Explorer from connecting.',
    default: 'full_indexed',
    values: {
      full_indexed: 'Full Indexed (required for Fulcrum and BCH Explorer)',
      normal:       'Normal (standard node, no full tx index)',
      pruned:       'Pruned (saves disk space, incompatible with Fulcrum and BCH Explorer)',
    },
  }),
  dbMaxSize: sdk.Value.number({
    name: 'Max Database Size',
    description:
      'Maximum blockchain database size in GB. Only applies when Database Mode is set to Pruned.',
    warning: null,
    required: false,
    default: null,
    min: 100,
    max: null,
    integer: true,
    units: 'GB',
    placeholder: '600',
  }),
  rpcEnabled: sdk.Value.toggle({
    name: 'JSON-RPC Server',
    description:
      'Expose the Bitcoin-Cash-compatible JSON-RPC interface (added in Knuth v1.3.0). ' +
      'Required by mining pools for getblocktemplatelight/submitblocklight. ' +
      'Credentials are generated automatically — see the RPC Credentials action.',
    default: false,
  }),
  ipcEnabled: sdk.Value.toggle({
    name: 'IPC (C-API) Capability',
    description:
      'Expose Knuth IPC/C-API capability for compatibility checks in dependent services.',
    default: true,
  }),
  utxozEnabled: sdk.Value.toggle({
    name: 'UTXOZ Capability',
    description:
      'Expose UTXOZ support capability for compatibility checks in dependent services.',
    default: true,
  }),
  torEnabled: sdk.Value.toggle({
    name: 'Tor Routing',
    description:
      'Route all outbound connections through the Tor network for enhanced privacy. ' +
      'Requires the Tor package to be installed and running.',
    default: false,
  }),
})
