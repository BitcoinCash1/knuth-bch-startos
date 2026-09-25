import { sdk } from '../../sdk'
import { knuthConf } from '../../fileModels/knuth.conf'
import { i18n } from '../../i18n'

const { InputSpec, Value } = sdk

// Mirrors BCHN's "RPC & Peers Settings" group, mapped onto kth v1.3.0 flags.
// RPC bind/port/credentials are managed by the Network action and the Credentials
// group, so this covers server tuning, peer connection limits and bandwidth.
const rpcPeersSpec = InputSpec.of({
  outboundConnections: Value.number({
    name: i18n('Outbound Connections'),
    description: i18n('Target number of outbound peer connections.'),
    warning: null,
    required: true,
    default: 8,
    min: 0,
    max: 100,
    integer: true,
    units: null,
  }),
  inboundConnections: Value.number({
    name: i18n('Inbound Connections'),
    description: i18n('Maximum number of inbound peer connections accepted.'),
    warning: null,
    required: true,
    default: 32,
    min: 0,
    max: 1000,
    integer: true,
    units: null,
  }),
  netThreads: Value.number({
    name: i18n('Network Threads'),
    description: i18n('Threads dedicated to networking. 0 lets Knuth choose.'),
    warning: null,
    required: true,
    default: 0,
    min: 0,
    max: 64,
    integer: true,
    units: null,
  }),
  hostPoolCapacity: Value.number({
    name: i18n('Host Pool Capacity'),
    description: i18n('Number of peer addresses kept in the host pool (peers.dat).'),
    warning: null,
    required: true,
    default: 10000,
    min: 0,
    max: 1000000,
    integer: true,
    units: 'addresses',
  }),
  connectBatchSize: Value.number({
    name: i18n('Connect Batch Size'),
    description: i18n('Number of connection attempts made in parallel.'),
    warning: null,
    required: true,
    default: 5,
    min: 1,
    max: 100,
    integer: true,
    units: null,
  }),
  connectTimeoutSeconds: Value.number({
    name: i18n('Connect Timeout'),
    description: i18n('Seconds to wait for a peer connection to be established.'),
    warning: null,
    required: true,
    default: 5,
    min: 1,
    max: 300,
    integer: true,
    units: 'seconds',
  }),
  channelInactivityMinutes: Value.number({
    name: i18n('Peer Inactivity Timeout'),
    description: i18n('Drop a peer after this many minutes with no traffic.'),
    warning: null,
    required: true,
    default: 10,
    min: 1,
    max: 1440,
    integer: true,
    units: 'minutes',
  }),
  useIpv6: Value.toggle({
    name: i18n('Enable IPv6'),
    description: i18n('Allow connections to peers over IPv6.'),
    default: false,
  }),
  gbtCacheSize: Value.number({
    name: i18n('GBT Job Cache Size'),
    description:
      i18n('Number of getblocktemplatelight jobs kept cached for mining pools.'),
    warning: null,
    required: true,
    default: 10,
    min: 1,
    max: 1000,
    integer: true,
    units: 'jobs',
  }),
  gbtStoreTime: Value.number({
    name: i18n('GBT Job Retention'),
    description:
      i18n('Seconds a getblocktemplatelight job stays valid before it expires.'),
    warning: null,
    required: true,
    default: 3600,
    min: 60,
    max: 86400,
    integer: true,
    units: 'seconds',
  }),
})

export const rpcPeersSettings = sdk.Action.withInput(
  'rpc-peers-settings',

  async ({ effects: _effects }) => ({
    name: i18n('RPC & Peers Settings'),
    description:
      i18n('Configure RPC server tuning, peer connections, network restrictions, and bandwidth limits.'),
    warning: null,
    allowedStatuses: 'any',
    group: i18n('Configuration'),
    visibility: 'enabled',
  }),

  rpcPeersSpec,

  async ({ effects }) => {
    const conf = await knuthConf.read().once()
    return {
      outboundConnections: conf?.['net.outbound_connections'] ?? 8,
      inboundConnections: conf?.['net.inbound_connections'] ?? 32,
      netThreads: conf?.['net.threads'] ?? 0,
      hostPoolCapacity: conf?.['net.host_pool_capacity'] ?? 10000,
      connectBatchSize: conf?.['net.connect_batch_size'] ?? 5,
      connectTimeoutSeconds: conf?.['net.connect_timeout_seconds'] ?? 5,
      channelInactivityMinutes: conf?.['net.channel_inactivity_minutes'] ?? 10,
      useIpv6: conf?.['net.use_ipv6'] ?? false,
      gbtCacheSize: conf?.['rpc.gbt_cache_size'] ?? 10,
      gbtStoreTime: conf?.['rpc.gbt_store_time'] ?? 3600,
    }
  },

  async ({ effects, input }) => {
    await knuthConf.merge(effects, {
      'net.outbound_connections': input.outboundConnections,
      'net.inbound_connections': input.inboundConnections,
      'net.threads': input.netThreads,
      'net.host_pool_capacity': input.hostPoolCapacity,
      'net.connect_batch_size': input.connectBatchSize,
      'net.connect_timeout_seconds': input.connectTimeoutSeconds,
      'net.channel_inactivity_minutes': input.channelInactivityMinutes,
      'net.use_ipv6': input.useIpv6,
      'rpc.gbt_cache_size': input.gbtCacheSize,
      'rpc.gbt_store_time': input.gbtStoreTime,
    })
    await effects.restart()
    return null
  },
)
