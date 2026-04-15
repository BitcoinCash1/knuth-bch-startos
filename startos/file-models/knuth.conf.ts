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

  // [database]
  'database.directory': z.string().catch('/data/blockchain'),
  'database.db_max_size': iniNumber.catch(600000000000),
  'database.safe_mode': z.boolean().catch(true),
  'database.cache_capacity': iniNumber.catch(10000),

  // [blockchain]
  'blockchain.cores': iniNumber.catch(0),

  // [node]
  'node.compact_blocks_high_bandwidth': z.boolean().catch(true),
  'node.ds_proofs': z.boolean().catch(true),
  'node.relay_transactions': z.boolean().catch(true),
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
  torEnabled: sdk.Value.toggle({
    name: 'Tor Routing',
    description:
      'Route all outbound connections through the Tor network for enhanced privacy. ' +
      'Requires the Tor package to be installed and running.',
    default: false,
  }),
})
