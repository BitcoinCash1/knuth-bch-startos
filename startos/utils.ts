import { randomBytes } from 'node:crypto'

export const generateRpcPassword = (): string => randomBytes(32).toString('hex')

export const peerInterfaceId = 'peer'
export const rpcInterfaceId = 'rpc'
export const rootDir = '/data'

// ── Network types ─────────────────────────────────────────────────────────────
// Keep Tempnet after Chipnet in the network dropdowns.
export const NETWORKS = [
  'mainnet',
  'testnet3',
  'testnet4',
  'scalenet',
  'chipnet',
  'tempnet',
  'regtest',
] as const
export type Network = (typeof NETWORKS)[number]

// Field order matches BCHN utils (rpc, peer).
export const networkPorts: Record<Network, { rpc: number; peer: number }> = {
  mainnet: { rpc: 8332, peer: 8333 },
  testnet3: { rpc: 18332, peer: 18333 },
  testnet4: { rpc: 28332, peer: 28333 },
  scalenet: { rpc: 38332, peer: 38333 },
  chipnet: { rpc: 48332, peer: 48333 },
  tempnet: { rpc: 48332, peer: 48333 },
  regtest: { rpc: 18443, peer: 18444 },
}

// kth v1.3.0 selects the network with a valued option (`--network <name>` / `-n`),
// not per-network boolean flags. The old `--chipnet` style flags are accepted and
// silently ignored, leaving the node on mainnet — verified against the binary:
// `--network chipnet` logs "Network: Chipnet" and seeds chipnet.imaginary.cash:48333,
// while `--chipnet` seeds mainnet on :8333.
export const networkName: Record<Network, string> = {
  mainnet: 'mainnet',
  testnet3: 'testnet',
  testnet4: 'testnet4',
  scalenet: 'scalenet',
  chipnet: 'chipnet',
  tempnet: 'tempnet',
  regtest: 'regtest',
}

// Same layout as BCHN/BCHD/Flowee on StartOS:
//   mainnet  → chain under /data/blockchain, peers at /data/peers.dat
//   testnets → /data/<network>/… (matches deleteTestNetworkData paths)
// Without this, switching mainnet↔chipnet reuses one chainstate and one hosts
// pool — peers get banned for the wrong network magic.
export const networkDbDir = (network: Network): string =>
  network === 'mainnet' ? `${rootDir}/blockchain` : `${rootDir}/${network}`

export const networkHostsFile = (network: Network): string =>
  network === 'mainnet'
    ? `${rootDir}/peers.dat`
    : network === 'tempnet'
      // The Tempnet binary uses net.hosts_file for its unified peer database.
      ? `${rootDir}/tempnet/peers-tempnet.dat`
      : `${rootDir}/${network}/peers.dat`

// Tempnet shares chipnet's network magic, ports and DNS seeds. Those seeds
// return chipnet nodes, which Tempnet rejects at the fork (block 327,989, as
// "bad-diffbits") and bans, so a node that only finds them stalls there with no
// peers. These are the known Tempnet nodes from the Tempnet BCHN build's
// TEMPNET.md (github.com/9500/bchn-tempnet, list of 2026-10-18), kept as
// persistent peers. Remove them with the rest of Tempnet after 2026-11-15.
export const tempnetPeers: readonly string[] = [
  '15.204.253.47:48335',
  '15.204.228.134:48333',
  '135.148.26.244:48333',
]

// ── Port (mainnet default, kept for backward compat) ──────────────────────────
export const peerPort = networkPorts.mainnet.peer
