import { FileHelper, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'

const iniNumber = z.union([z.string().transform(Number), z.number()])

export const shape = z.object({
  // [node]
  'node.compact_blocks': z.literal(true).catch(true),
  'node.ds_proofs': z.literal(true).catch(true),
  'node.transaction_pool_check_ratio': iniNumber.catch(500),

  // [blockchain]
  'blockchain.checkpoint': z.literal(true).catch(true),
  'blockchain.fix_checkpoints': z.literal(true).catch(true),

  // [network]
  'network.inbound_port': iniNumber.catch(8333),
  'network.inbound_connections': iniNumber.catch(32),
  'network.outbound_connections': iniNumber.catch(8),

  // [database]
  'database.directory': z.string().catch('/data/db'),
  'database.db_max_size': iniNumber.catch(600),
  'database.safe_mode': z.literal(true).catch(true),

  // RPC
  rpcuser: z.string().catch('knuth-bch'),
  rpcpassword: z.string().catch(''),
  rpcallowip: z.string().catch('0.0.0.0/0'),
  rpcbind: z.string().catch('0.0.0.0'),
  rpcport: iniNumber.catch(8332),

  // ZMQ
  zmqpubhashblock: z.string().catch('tcp://0.0.0.0:28332'),
  zmqpubrawtx: z.string().catch('tcp://0.0.0.0:28333'),
  zmqpubhashtx: z.string().catch('tcp://0.0.0.0:28334'),
  zmqpubrawblock: z.string().catch('tcp://0.0.0.0:28335'),

  // General
  txindex: z.literal(true).catch(true),
  server: z.literal(true).catch(true),
  listen: z.literal(true).catch(true),
  dbcache: iniNumber.catch(512),
  maxconnections: iniNumber.catch(125),
})

export const knuthConf = FileHelper.ini(
  {
    base: sdk.volumes.main,
    subpath: 'kth-node.cfg',
  },
  shape,
)

// Config spec for user-facing settings
export const fullConfigSpec = sdk.InputSpec.of({
  zmqEnabled: sdk.Value.toggle({
    name: 'ZMQ Notifications',
    description:
      'Enable ZMQ pub/sub notifications for blocks and transactions. Required by Fulcrum, Explorer, and other dependent services.',
    default: true,
  }),
  txindex: sdk.Value.toggle({
    name: 'Transaction Index',
    description:
      'Knuth always maintains a full transaction index. This cannot be disabled.',
    default: true,
  }),
  prune: sdk.Value.number({
    name: 'Prune (MB)',
    description:
      'Knuth does not support block pruning. This field exists for compatibility with dependent packages.',
    required: false,
    default: null,
    min: 0,
    max: 0,
    integer: true,
    units: 'MB',
  }),
  dbcache: sdk.Value.number({
    name: 'Database Cache (MB)',
    description: 'RAM allocated to the database cache.',
    required: true,
    default: 512,
    min: 64,
    max: 16384,
    integer: true,
    units: 'MB',
  }),
  maxconnections: sdk.Value.number({
    name: 'Max Connections',
    description: 'Maximum number of peer connections.',
    required: true,
    default: 125,
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
