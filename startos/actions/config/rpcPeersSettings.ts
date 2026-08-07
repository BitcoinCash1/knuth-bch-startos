import { sdk } from '../../sdk'
import { knuthConf } from '../../file-models/knuth.conf'

const { InputSpec, Value } = sdk

// Mirrors BCHN's "RPC & Peers Settings" group, mapped onto kth v1.3.0 flags.
// RPC bind/port/credentials are managed by the Network action and the Credentials
// group, so this covers server tuning, peer connection limits and bandwidth.
const rpcPeersSpec = InputSpec.of({
  outboundConnections: Value.number({
    name: 'Outbound Connections',
    description: 'Target number of outbound peer connections.',
    warning: null,
    required: true,
    default: 8,
    min: 0,
    max: 100,
    integer: true,
    units: null,
  }),
  inboundConnections: Value.number({
    name: 'Inbound Connections',
    description: 'Maximum number of inbound peer connections accepted.',
    warning: null,
    required: true,
    default: 32,
    min: 0,
    max: 1000,
    integer: true,
    units: null,
  }),
  netThreads: Value.number({
    name: 'Network Threads',
    description: 'Threads dedicated to networking. 0 lets Knuth choose.',
    warning: null,
    required: true,
    default: 0,
    min: 0,
    max: 64,
    integer: true,
    units: null,
  }),
  hostPoolCapacity: Value.number({
    name: 'Host Pool Capacity',
    description: 'Number of peer addresses kept in the host pool (peers.dat).',
    warning: null,
    required: true,
    default: 10000,
    min: 0,
    max: 1000000,
    integer: true,
    units: 'addresses',
  }),
  connectBatchSize: Value.number({
    name: 'Connect Batch Size',
    description: 'Number of connection attempts made in parallel.',
    warning: null,
    required: true,
    default: 5,
    min: 1,
    max: 100,
    integer: true,
    units: null,
  }),
  connectTimeoutSeconds: Value.number({
    name: 'Connect Timeout',
    description: 'Seconds to wait for a peer connection to be established.',
    warning: null,
    required: true,
    default: 5,
    min: 1,
    max: 300,
    integer: true,
    units: 'seconds',
  }),
  channelInactivityMinutes: Value.number({
    name: 'Peer Inactivity Timeout',
    description: 'Drop a peer after this many minutes with no traffic.',
    warning: null,
    required: true,
    default: 10,
    min: 1,
    max: 1440,
    integer: true,
    units: 'minutes',
  }),
  useIpv6: Value.toggle({
    name: 'Enable IPv6',
    description: 'Allow connections to peers over IPv6.',
    default: false,
  }),
  gbtCacheSize: Value.number({
    name: 'GBT Job Cache Size',
    description:
      'Number of getblocktemplatelight jobs kept cached for mining pools.',
    warning: null,
    required: true,
    default: 10,
    min: 1,
    max: 1000,
    integer: true,
    units: 'jobs',
  }),
  gbtStoreTime: Value.number({
    name: 'GBT Job Retention',
    description:
      'Seconds a getblocktemplatelight job stays valid before it expires.',
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
    name: 'RPC & Peers Settings',
    description:
      'Configure RPC server tuning, peer connections, network restrictions, and bandwidth limits.',
    warning: null,
    allowedStatuses: 'any',
    group: 'Configuration',
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
