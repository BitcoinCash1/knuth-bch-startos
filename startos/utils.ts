export const peerInterfaceId = 'peer'
export const rpcInterfaceId = 'rpc'
export const rootDir = '/data'

// ── Network types ─────────────────────────────────────────────────────────────
export const NETWORKS = ['mainnet', 'testnet3', 'testnet4', 'scalenet', 'chipnet', 'regtest'] as const
export type Network = (typeof NETWORKS)[number]

export const networkPorts: Record<Network, { peer: number; rpc: number }> = {
  mainnet:  { peer: 8333,  rpc: 8332  },
  testnet3: { peer: 18333, rpc: 18332 },
  testnet4: { peer: 28333, rpc: 28332 },
  scalenet: { peer: 38333, rpc: 38332 },
  chipnet:  { peer: 48333, rpc: 48332 },
  regtest:  { peer: 18444, rpc: 18443 },
}

export const networkFlag: Record<Network, string | null> = {
  mainnet:  null,
  testnet3: '--testnet',
  testnet4: '--testnet4',
  scalenet: '--scalenet',
  chipnet:  '--chipnet',
  regtest:  '--regtest',
}

// ── Port (mainnet default, kept for backward compat) ──────────────────────────
export const peerPort = networkPorts.mainnet.peer
