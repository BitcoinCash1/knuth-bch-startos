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
  'log.archive_directory': z.string().catch('/data/log-archive'),
  'log.rotation_size': iniNumber.catch(0),
  'log.minimum_free_space': iniNumber.catch(0),
  'log.maximum_archive_size': iniNumber.catch(0),
  'log.maximum_archive_files': iniNumber.catch(0),
  'log.statistics_server': z.string().catch(''),

  // [net]
  'net.inbound_port': iniNumber.catch(8333),
  'net.inbound_connections': iniNumber.catch(32),
  'net.outbound_connections': iniNumber.catch(8),

  // [net] hosts pool — kth defaults peers.dat to process CWD (/), which is
  // outside the StartOS volume. Pin it under /data (BCHN-style layout) so
  // bans/peers survive rebuilds and Delete Peer List works.
  'net.hosts_file': z.string().catch('/data/peers.dat'),

  // [db]
  // Mainnet: /data/blockchain. Testnets: /data/<network> (same schema as
  // BCHN/BCHD/Flowee + deleteTestNetworkData).
  'db.directory': z.string().catch('/data/blockchain'),
  'db.db_max_size': iniNumber.catch(600000000000),
  'db.safe_mode': z.boolean().catch(true),
  'db.cache_capacity': iniNumber.catch(10000),
  // v1.3.0 accepts pruned|blocks|full (verified against the binary; the old
  // full_indexed/normal spellings are rejected with "illegal value").
  'db.db_mode': z.enum(['full', 'blocks', 'pruned']).catch('full'),

  // [net] peers/tuning — exposed via "RPC & Peers Settings" (BCHN parity).
  'net.threads': iniNumber.catch(0),
  'net.protocol_maximum': iniNumber.catch(70016),
  'net.protocol_minimum': iniNumber.catch(70012),
  'net.host_pool_capacity': iniNumber.catch(10000),
  'net.connect_batch_size': iniNumber.catch(5),
  'net.connect_timeout_seconds': iniNumber.catch(5),
  'net.channel_handshake_seconds': iniNumber.catch(30),
  'net.channel_heartbeat_minutes': iniNumber.catch(5),
  'net.channel_inactivity_minutes': iniNumber.catch(10),
  'net.channel_expiration_minutes': iniNumber.catch(60),
  'net.channel_germination_seconds': iniNumber.catch(30),
  'net.manual_attempt_limit': iniNumber.catch(0),
  'net.validate_checksum': z.boolean().catch(false),
  // Protocol-level service bits and network magic. kth sets correct values per
  // network; declared so a hand-edited kth.cfg round-trips, not exposed in the UI.
  'net.services': iniNumber.optional(),
  'net.invalid_services': iniNumber.optional(),
  'net.identifier': iniNumber.optional(),
  'net.use_ipv6': z.boolean().catch(false),
  // Advertised public address; empty means "let kth decide".
  'net.self': z.string().catch(''),
  // List-valued options. kth accepts these repeated; FileHelper.ini round-trips
  // arrays, and an empty array simply writes no entry.
  'net.peer': z.array(z.string()).catch([]),
  'net.seed': z.array(z.string()).catch([]),
  'net.blacklist': z.array(z.string()).catch([]),
  'net.user_agent_blacklist': z.array(z.string()).catch([]),

  // [chain]
  'chain.cores': iniNumber.catch(0),
  'chain.priority': z.boolean().catch(true),
  'chain.reorganization_limit': iniNumber.catch(256),
  'chain.gbt_template_refresh_seconds': iniNumber.catch(5),
  'chain.fix_checkpoints': z.boolean().catch(true),
  'chain.checkpoint': z.array(z.string()).catch([]),

  // [fork] — consensus activation parameters. Deliberately NOT exposed in any
  // action: kth derives correct values per network, and overriding them would
  // fork this node off the real chain. Declared only so a user-supplied kth.cfg
  // round-trips instead of being silently dropped on the next merge.
  'fork.easy_blocks': z.boolean().optional(),
  'fork.retarget': z.boolean().optional(),
  'fork.asert_half_life': iniNumber.optional(),
  'fork.leibniz_activation_time': iniNumber.optional(),
  'fork.cantor_activation_time': iniNumber.optional(),

  // [db] mempool/reorg pool — "Mempool & Block Policy"
  'db.reorg_pool_limit': iniNumber.catch(100),

  // [node]
  'node.compact_blocks_high_bandwidth': z.boolean().catch(true),
  'node.refresh_transactions': z.boolean().catch(true),
  'node.ds_proofs': z.boolean().catch(true),
  'node.relay_transactions': z.boolean().catch(true),
  'node.block_latency_seconds': iniNumber.catch(60),
  'node.notify_limit_hours': iniNumber.catch(24),
  // kth takes these as FLOAT (satoshis per byte / per sigop).
  'node.byte_fee_satoshis': z.union([z.string().transform(Number), z.number()]).catch(1),
  'node.sigop_fee_satoshis': z.union([z.string().transform(Number), z.number()]).catch(100),
  'node.minimum_output_satoshis': iniNumber.catch(546),
  // Display mode: tui | log | daemon. Must stay "log" under StartOS — the TUI
  // expects a terminal and would break log capture.
  'node.display': z.enum(['tui', 'log', 'daemon']).catch('log'),

  // [fork] BIP activation toggles — consensus, never exposed (see note below).
  'fork.bip16': z.boolean().optional(),
  'fork.bip30': z.boolean().optional(),
  'fork.bip34': z.boolean().optional(),
  'fork.bip66': z.boolean().optional(),
  'fork.bip65': z.boolean().optional(),
  'fork.bip90': z.boolean().optional(),
  'fork.bip68': z.boolean().optional(),
  'fork.bip112': z.boolean().optional(),
  'fork.bip113': z.boolean().optional(),

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
    default: 'full',
    values: {
      full: 'Full Indexed (required for Fulcrum and BCH Explorer)',
      blocks:       'Blocks (standard node, no full tx index)',
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
      'Prefer Tor for outbound peer connections when the Tor package is installed ' +
      'and running (same optional dependency pattern as BCHN/BCHD/Flowee). ' +
      'Knuth does not yet expose SOCKS/onion CLI flags equivalent to BCHN; this ' +
      'toggle marks the Tor dependency and surfaces Tor health. Inbound .onion ' +
      'is published via StartOS Interfaces → Peer → Add Onion Service.',
    default: false,
  }),
})
