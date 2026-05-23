# Knuth BCH — StartOS Package

High-performance C++ Bitcoin Cash full node for StartOS.

## Features

- **P2P full node** — validates and syncs the Bitcoin Cash blockchain
- **Network selection** — mainnet, testnet3, testnet4, scalenet, chipnet, regtest
- **Node Settings action** — manage database mode, connections, and runtime settings from StartOS UI
- **Database mode** — full_indexed (required for Fulcrum/Explorer), normal, or pruned
- **IPC via C-API capability** — enabled by default in Node Settings
- **UTXOZ capability** — enabled by default in Node Settings
- **Tor support** — optional routing through Tor network
- **No JSON-RPC/gRPC in this package version** — upcoming in future upstream releases

## Network Ports

| Network  | P2P Port |
|----------|----------|
| mainnet  | 8333     |
| testnet3 | 18333    |
| testnet4 | 28333    |
| scalenet | 38333    |
| chipnet  | 48333    |
| regtest  | 18444    |

## Actions (StartOS UI)

| Action                   | Group         | Description                                     |
|--------------------------|---------------|-------------------------------------------------|
| Node Info                | —             | Display network, sync status, block count       |
| Network                  | Configuration | Select BCH network (mainnet/testnets)           |
| Node Settings            | Configuration | Database mode, connections, logging, IPC/UTXOZ  |
| RPC Credentials          | Credentials   | Coming soon (grayed out)                        |
| Delete Peer List         | Maintenance   | Remove peers.dat, rebuild from DNS seeds        |
| Delete Test Network Data | Maintenance   | Free disk space for selected test networks      |

## Compatibility

This package intentionally exposes only currently-supported Knuth features.

| Feature | Knuth | BCHN | BCHD |
|---------|-------|------|------|
| JSON-RPC | ❌ (coming soon) | ✅ | ✅ |
| ZMQ | ❌ (not exposed in package yet) | ✅ | ❌ |
| gRPC | ❌ | ❌ | ✅ |
| IPC/C-API | ✅ | ❌ | ❌ |
| Compact Filters | ❌ | ✅ | ✅ |
| UTXO Queries | ✅ | ✅ | ✅ |
| Fulcrum Compatible | ❌ (until JSON-RPC exists) | ✅ | ✅ |
| Mining Pool Compatible | ❌ (until JSON-RPC exists) | ✅ | ✅ |

## Upstream

- Source: https://github.com/k-nuth/kth
- Website: https://kth.cash
