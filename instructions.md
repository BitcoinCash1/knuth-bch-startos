# Knuth

Knuth is a high-performance Bitcoin Cash full node written in C++. This page covers
what is specific to running it on StartOS once it is installed.

## Documentation

- [Knuth upstream](https://github.com/k-nuth/kth) — source, releases, and operator docs
- [kth.cash](https://kth.cash) — project site
- [JSON-RPC (v1.3.0+)](https://github.com/k-nuth/kth/blob/master/docs/json-rpc.md) — methods including mining (`getblocktemplatelight` / `submitblocklight`)

## What you get on StartOS

- The upstream **Knuth Bitcoin Cash node** with its original consensus and relay policy
- **P2P** (port follows the selected network)
- Optional **JSON-RPC**, served by Knuth itself
- The same multi-network layout as BCHN / BCHD / Flowee: mainnet under `/data/blockchain`, testnets under `/data/<network>/`
- Optional **Tor** package integration; outbound peer traffic currently uses clearnet

## Getting started

1. Install and start Knuth — Initial Block Download begins immediately on mainnet.
2. Watch the **Dashboard** health checks — the same rows as BCHN / BCHD / Flowee: **RPC**, **Blockchain Sync** (percent), **Peer Connections**, **Tor** (optional), **I2P**, **Clearnet**.
3. When you need RPC (pools, Fulcrum, Explorer): **Config → Node Settings → JSON-RPC Server**, then **Actions → RPC Credentials**.

## Moving from Knuth 1.3.x

Knuth 1.4.0 changed the UTXO-Z database format, so 1.4.0 and later need a fresh
blockchain database when you come from 1.3.x. For an existing installation:

1. Stop Knuth.
2. Run **Actions → Rebuild Blockchain Database** for the active network.
3. Install or sideload the new package, then start Knuth and let it download and
   validate the chain again.

The rebuild deletes chain data for the selected network and preserves your
network settings and RPC credentials. Repeat it before using another network
that still has a 1.3.x database. A fresh installation already has an empty
database. Keep a 1.3.x chain database out of restores into 1.4.0 or later.

Coming from 1.4.0, Knuth 1.5.0 reuses the existing database; no rebuild is needed.

To use Chipnet, select **Config → Network → chipnet** and enable JSON-RPC so the
dashboard can verify synchronization. Native x86_64 execution is available;
ARM64 and RISC-V packages emulate the x86_64 node.

## JSON-RPC

Off by default. Credentials are generated from a cryptographic random source at
install and stay stable until you rotate them. Enabling RPC or changing networks
refreshes the corresponding StartOS interface automatically.

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

**Tor is optional.** Its dependency becomes active when Tor Routing is enabled.
The toggle currently advertises the dependency and its health status; it does not
configure an outbound SOCKS proxy in Knuth. Outbound peer traffic uses clearnet.
For inbound onion access, use **Interfaces → Peer Interface → Add Onion Service**.

## Maintenance

- **Delete Peer List** — stop the service first, then run this to reset peer discovery and unban seeds. Stop now kills the node within 45 seconds.
- **Delete Test Network Data** — stop first, then wipe selected test-network chain data (including the network you are currently on). Mainnet is never touched.
- **Rebuild Blockchain Database** — stop first, then delete all chain databases for the active network and download the chain again. Network settings and RPC credentials are kept.
- **RPC Credentials** — username, password, port
- **Node Info** — runtime summary

## Limitations

- Sync health uses Knuth's validated block height and hash. Any gap behind headers
  stays loading. Public networks also require fresh peer status and a recent,
  readable tip. With JSON-RPC disabled, the node may run, but the dashboard cannot
  verify synchronization.
- Native execution is available on x86_64. ARM64 and RISC-V installations use
  emulation of the x86_64 image, which can substantially slow initial sync and
  signature validation.
- JSON-RPC is served by Knuth itself, with no proxy. A method the Knuth release
  does not implement returns Knuth's own error.
- Blockchain data is not included in StartOS backups — after restore the node re-syncs.

## Package updates

The BitcoinCash1 repository checks new stable upstream releases daily. A new
version must pass a binary-version check and a running JSON-RPC check before its
package version is updated and a release build is requested. The package keeps
upstream node behavior and adds no transaction-size rule.

Maintainers can build a sideload package from **Actions → Build → Run workflow**.
Choose the branch and architecture target, then download its `.s9pk` and
`SHA256SUMS` artifact. ARM64 and RISC-V targets remain emulated. Future automatic
builds do not automatically erase an installed node's database or prove that a
future database format can be reused.
