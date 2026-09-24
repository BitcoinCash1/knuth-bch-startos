# Knuth

Knuth is a high-performance Bitcoin Cash full node written in C++. This page covers
what is specific to running it on StartOS once it is installed.

## Documentation

- [Knuth upstream](https://github.com/k-nuth/kth) — source, releases, and operator docs
- [kth.cash](https://kth.cash) — project site
- [JSON-RPC (v1.3.0+)](https://github.com/k-nuth/kth/blob/master/docs/json-rpc.md) — methods including mining (`getblocktemplatelight` / `submitblocklight`)

## What you get on StartOS

- A **Bitcoin Cash full node** that validates and relays blocks and transactions
- **P2P** (port follows the selected network)
- Optional **JSON-RPC** (Bitcoin-Cash-compatible) for mining pools, Fulcrum, and explorers
- The same multi-network layout as BCHN / BCHD / Flowee: mainnet under `/data/blockchain`, testnets under `/data/<network>/`
- Optional **Tor** routing when the Tor package is installed

## Getting started

1. Install and start Knuth — Initial Block Download begins immediately on mainnet.
2. Watch the **Dashboard** health checks — the same rows as BCHN / BCHD / Flowee: **RPC**, **Blockchain Sync** (percent), **Peer Connections**, **Tor** (optional), **I2P**, **Clearnet**.
3. When you need RPC (pools, Fulcrum, Explorer): **Config → Node Settings → JSON-RPC Server**, then **Actions → RPC Credentials**.

## JSON-RPC

Off by default. Credentials are generated at install and stay stable.

| Network  | P2P   | RPC   |
|----------|-------|-------|
| mainnet  | 8333  | 8332  |
| testnet3 | 18333 | 18332 |
| testnet4 | 28333 | 28332 |
| scalenet | 38333 | 38332 |
| chipnet  | 48333 | 48332 |
| regtest  | 18444 | 18443 |

Use the **Interfaces** tab for the RPC endpoint other services should call. gRPC is not exposed in this package.

## Configuration

- **Network** — mainnet (default), testnet3, testnet4, scalenet, chipnet, regtest. Switches data directory, P2P port, and RPC port; node restarts automatically. Mainnet data is kept separately when you leave and return.
- **Node Settings** — database mode (`full` / `blocks` / `pruned`), connections, logging, JSON-RPC, UTXO-Z, IPC, Tor.

`full` database mode is required for Fulcrum and BCH Explorer.

## Tor

**Tor is optional** (same as the other BCH nodes). It always shows on the Knuth **Dependencies** tab. Knuth runs on clearnet without Tor running; install the Tor package and enable **Tor Routing** in Node Settings only if you want outbound peer traffic through Tor. For inbound onion: **Interfaces → Peer Interface → Add Onion Service**.

## Maintenance

- **Delete Peer List** — stop the service first, then run this to reset peer discovery and unban seeds. Stop now kills the node within 45 seconds.
- **Delete Test Network Data** — stop first, then wipe selected test-network chain data (including the network you are currently on). Mainnet is never touched.
- **RPC Credentials** — username, password, port
- **Node Info** — runtime summary

## Limitations

- Sync progress comes from Knuth's own log (`Fully synced at height`). The JSON-RPC `blocks` field often stays 0 even at the tip.
- The official upstream container image must be built with `rpc=True` for the JSON-RPC server to exist at all; this package expects that.
- Blockchain data is not included in StartOS backups — after restore the node re-syncs.
