export const peerInterfaceId = 'peer'
export const rootDir = '/data'

// ── Network types ─────────────────────────────────────────────────────────────
export const NETWORKS = ['mainnet', 'testnet3', 'testnet4', 'scalenet', 'chipnet', 'regtest'] as const
export type Network = (typeof NETWORKS)[number]

export const networkPorts: Record<Network, { peer: number }> = {
  mainnet:  { peer: 8333  },
  testnet3: { peer: 18333 },
  testnet4: { peer: 28333 },
  scalenet: { peer: 38333 },
  chipnet:  { peer: 48333 },
  regtest:  { peer: 18444 },
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
