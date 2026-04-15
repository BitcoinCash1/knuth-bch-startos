# Knuth BCH — StartOS Package

High-performance C++ Bitcoin Cash full node for StartOS.

## Features

- **Bitcoin Core-compatible JSON-RPC** — works with all services that support BCHN
- **ZMQ notifications** — `zmqpubhashblock`, `zmqpubrawtx`, `zmqpubhashtx`, `zmqpubrawblock`
- **IPC via C-API** — low-latency inter-process communication
- **UTXO queries** — built-in UTXO set queries
- **Tor support** — optional routing through Tor network
- **Full transaction index** — always enabled

## Compatibility

Knuth provides the same JSON-RPC + ZMQ interface as BCHN. Services that work with BCHN (Fulcrum, Explorer, mining pools) also work with Knuth.

| Feature | Knuth | BCHN | BCHD |
|---------|-------|------|------|
| JSON-RPC | ✅ | ✅ | ❌ (limited) |
| ZMQ | ✅ | ✅ | ❌ |
| gRPC | ❌ | ❌ | ✅ |
| IPC/C-API | ✅ | ❌ | ❌ |
| Compact Filters | ❌ | ✅ | ✅ |
| UTXO Queries | ✅ | ✅ | ✅ |
| Fulcrum Compatible | ✅ | ✅ | ❌ |
| Mining Pool Compatible | ✅ | ✅ | ✅ |

## Upstream

- Source: https://github.com/k-nuth/kth
- Website: https://kth.cash
