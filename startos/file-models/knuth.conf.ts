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
  compactBlocksHighBandwidth: sdk.Value.toggle({
    name: 'Compact Blocks High Bandwidth',
    description: 'Keep high-bandwidth compact block relay enabled.',
    default: true,
  }),
  dsProofsEnabled: sdk.Value.toggle({
    name: 'Double-Spend Proofs',
    description: 'Enable double-spend proof processing in the node core.',
    default: true,
  }),
  relayTransactions: sdk.Value.toggle({
    name: 'Relay Transactions',
    description: 'Relay unconfirmed transactions to peers.',
    default: true,
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
