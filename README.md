# Knuth BCH — StartOS Package

High-performance C++ Bitcoin Cash full node for StartOS.

## Features

- **P2P full node** — validates and syncs the Bitcoin Cash blockchain
- **Node Settings action** — manage supported runtime settings from StartOS UI
- **IPC via C-API capability** — enabled by default in Node Settings
- **UTXOZ capability** — enabled by default in Node Settings
- **Tor support** — optional routing through Tor network
- **No JSON-RPC/gRPC in this package version** — upcoming in future upstream releases

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
