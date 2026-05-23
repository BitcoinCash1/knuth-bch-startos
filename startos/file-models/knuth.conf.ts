import { FileHelper, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'

const iniNumber = z.union([z.string().transform(Number), z.number()])

export const shape = z.object({
  // [log]
  'log.debug_file': z.string().catch('/data/debug.log'),
  'log.error_file': z.string().catch('/data/error.log'),
  'log.verbose': z.boolean().catch(false),

  // [network]
  'network.inbound_port': iniNumber.catch(8333),
  'network.inbound_connections': iniNumber.catch(32),
  'network.outbound_connections': iniNumber.catch(8),
  'network.relay_transactions': z.boolean().catch(true),

  // [database]
  'database.directory': z.string().catch('/data/blockchain'),
  'database.db_max_size': iniNumber.catch(600000000000),
  'database.safe_mode': z.boolean().catch(true),
  'database.cache_capacity': iniNumber.catch(10000),

  // [blockchain]
  'blockchain.cores': iniNumber.catch(0),

  // [node]
  'node.compact_blocks_high_bandwidth': z.boolean().catch(true),
  'node.sync_peers': iniNumber.catch(0),
  'node.sync_timeout_seconds': iniNumber.catch(5),
  'node.refresh_transactions': z.boolean().catch(true),
  'node.ds_proofs_enabled': z.boolean().catch(true),
  'node.ds_proofs': z.boolean().catch(true),
  'node.block_latency_seconds': iniNumber.catch(60),

  // [database] — mode selection
  'database.db_mode': z.enum(['full_indexed', 'normal', 'pruned']).catch('full_indexed'),
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
