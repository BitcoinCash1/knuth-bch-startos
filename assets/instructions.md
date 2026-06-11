# Knuth

Knuth is a high-performance Bitcoin Cash full node written in C++ with a modular
architecture. It is designed for speed and extensibility. This page covers what is
specific to running it on StartOS.

## What you get on StartOS

- A **Bitcoin Cash full node** that validates and relays blocks and transactions.
- **P2P interface** (port adjusts per network) for peer connections.
- Modular architecture — enable or disable UTXO-Z (alternative UTXO storage engine),
  IPC (inter-process communication), and Tor via Config.
- Multiple networks: **mainnet**, **testnet3**, **testnet4**, **scalenet**, **chipnet**,
  and **regtest**.

## Getting started

Knuth begins its Initial Block Download — fetching and verifying the entire BCH chain —
the moment it launches; nothing needs configuring first.

1. Install Knuth.
2. Open the **Dashboard** to watch sync progress. IBD time depends on your hardware
   and internet connection.
3. Once synced, Knuth validates and relays new blocks and transactions.

## RPC status

**JSON-RPC and gRPC are not yet available** in the current upstream Knuth release.
Knuth syncs and validates the blockchain, but services that require RPC access —
mining pools (ASICSeer, EloPool), Fulcrum BCH, BCH Explorer — cannot connect to
Knuth as a node backend until upstream ships the RPC module.

When RPC ships upstream, Knuth will be wired into the same dependency graph as BCHN,
BCHD, and Flowee with no further packaging changes required.

## Configuration

All settings are available under **Config**:

- **Network** — mainnet (default), testnet3, testnet4, scalenet, chipnet, or regtest.
  Changing network switches the data directory and P2P port. The node restarts
  automatically.
- **UTXO-Z** — enable the alternative UTXO storage engine (experimental).
- **IPC** — enable inter-process communication module.
- **Tor** — route outbound peer connections through Tor when Tor is installed.

## Ports

| Network  | P2P port |
|----------|----------|
| mainnet  | 8333     |
| testnet3 | 18333    |
| testnet4 | 28333    |
| scalenet | 38333    |
| chipnet  | 48333    |
| regtest  | 18444    |

Note: Knuth does not yet expose an RPC port. The P2P port is the only active
inbound interface.

## Switching networks

Use **Config → Network** to change the active network. The node restarts
automatically and starts syncing the new network from scratch. Each network uses
a separate data directory — switching back to mainnet resumes from where mainnet
left off.

## Tor networking

When Tor is installed and **Tor** is enabled in Config, Knuth routes outbound peer
connections through Tor. This hides your IP from peers and from anyone observing
your network traffic.

For inbound onion connectivity: open **Interfaces → Peer Interface → Add Onion Service**
in StartOS. This creates a hidden service at a `.onion` address pointing to Knuth's
P2P port.

## Maintenance actions

- **Delete Peer List** — clear the stored peer list and force re-discovery.
- **Delete Test Network Data** — remove data for testnet3, testnet4, scalenet,
  chipnet, or regtest while preserving mainnet data.
- **Node Info** — display current block height, sync status, and connected peers.

## Limitations

- **RPC is not yet implemented upstream.** Dependent services (pools, Fulcrum, BCH
  Explorer) cannot connect to Knuth until upstream ships the RPC module.
- IBD must complete before dependent services can start (once RPC is available).
- Backing up Knuth only saves configuration. Blockchain data is not backed up and
  must re-sync from scratch after a restore.

## Support

- Package: <https://github.com/BitcoinCash1/knuth-bch-startos>
- Upstream: <https://github.com/k-nuth/kth>
