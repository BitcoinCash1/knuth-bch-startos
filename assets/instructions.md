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
2. Watch the **Dashboard** health checks. With JSON-RPC enabled, **Blockchain Sync** shows height vs headers.
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

Enable **Tor** in Node Settings when the Tor package is installed and running. For inbound onion: **Interfaces → Peer Interface → Add Onion Service**.

## Maintenance

- **Delete Peer List** — reset peer discovery (service must be stopped)
- **Delete Test Network Data** — wipe selected test-network chain data without touching mainnet
- **RPC Credentials** — username, password, port
- **Node Info** — runtime summary

## Limitations

- Sync progress in the UI requires JSON-RPC enabled (Knuth has no separate IBD progress field when RPC is off).
- The official upstream container image must be built with `rpc=True` for the JSON-RPC server to exist at all; this package expects that.
- Blockchain data is not included in StartOS backups — after restore the node re-syncs.
