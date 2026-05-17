# Knuth

Knuth is a high-performance Bitcoin Cash full node written in C++ with a modular
architecture. It is designed for speed and extensibility with support for optional
modules such as compact block filters (BIP 157/158) and a forthcoming gRPC/JSON-RPC
interface.

## What you get on StartOS

- A **Bitcoin Cash full node** that validates and relays blocks and transactions.
- **P2P** (port 8333) for peer connections.
- Modular architecture — enable or disable UTXO-Z (UTXO database), IPC, and other
  capabilities via Config.

## Getting started

1. Install Knuth.
2. Knuth will begin its Initial Block Download. IBD time depends on your hardware
   and internet connection.
3. Watch progress on the **Dashboard**.

## Configuration

All settings are available under **Config**:

- **Network** — mainnet (default), testnet, chipnet.
- **UTXO-Z** — enable the alternative UTXO storage engine.
- **IPC** — inter-process communication module.

## RPC status

JSON-RPC and gRPC are **not yet available** in the current upstream Knuth release.
Services that require RPC access (mining pools, Fulcrum) cannot use Knuth as a
backend until upstream ships the RPC module. Selecting Knuth as a node backend in
dependent services will surface a clear RPC error at startup.

## Ports

| Port | Protocol | Purpose          |
|------|----------|------------------|
| 8333 | TCP      | P2P (peer relay) |

## Limitations

- RPC is not yet implemented upstream. Dependent services (pools, Fulcrum, BCH
  Explorer) cannot connect to Knuth until the upstream RPC module ships.
- IBD must complete before dependent services can start.

## Support

- Package: <https://github.com/BitcoinCash1/knuth-bch-startos>
- Upstream: <https://github.com/k-nuth/kth>
