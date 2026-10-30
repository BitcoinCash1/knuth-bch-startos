<div align="center">
  <img src="icon.svg" alt="Knuth logo" width="21%" />
  <h1>Knuth</h1>
</div>

> **Upstream docs:** [github.com/k-nuth/kth](https://github.com/k-nuth/kth) · [kth.cash](https://kth.cash)
>
> Knuth is a high-performance Bitcoin Cash full node written in C++. This StartOS
> package mirrors the BCHN / BCHD / Flowee schema: per-network ports, optional
> JSON-RPC (v1.3.0+), Tor dependency, and test-network data isolation under
> `/data/<network>/`.
>
> **Versioning:** the number before `:` is the upstream release; the number
> after it is the StartOS package revision. The package manifest is the source
> of truth for the installed version.

---

## Table of Contents

1. [Image and Container Runtime](#1-image-and-container-runtime)
2. [Volume and Data Layout](#2-volume-and-data-layout)
3. [Installation and First-Run Flow](#3-installation-and-first-run-flow)
4. [Default Networking](#4-default-networking)
5. [Configuration Management](#5-configuration-management)
6. [Network Access and Interfaces](#6-network-access-and-interfaces)
7. [Actions (StartOS UI)](#7-actions-startos-ui)
8. [Backups and Restore](#8-backups-and-restore)
9. [Health Checks](#9-health-checks)
10. [Dependencies](#10-dependencies)
11. [Default Overrides](#11-default-overrides)
12. [Limitations and Differences](#12-limitations-and-differences)
13. [What Is Unchanged from Upstream](#13-what-is-unchanged-from-upstream)
14. [Contributing](#14-contributing)
15. [Quick Reference for AI Consumers](#15-quick-reference-for-ai-consumers)

---

## 1. Image and Container Runtime

| Field | Value |
|---|---|
| **Image ID** | `knuth` |
| **Build** | `Dockerfile` builds the pinned Knuth release with RPC enabled and copies its matching GCC runtime |
| **Architectures** | Native `x86_64`; `aarch64` and `riscv64` use StartOS emulation of that image (`emulateMissing`). They are not native ARM or RISC-V builds. |
| **Command** | `kth -c /data/kth.cfg --init_run --network <name>` |

The node build follows the RPC-enabled recipe merged in
[k-nuth/docker-images#7](https://github.com/k-nuth/docker-images/pull/7), commit
`54a66910d7acda68766899a75cbb0304d617cb0a`, with its pinned GCC toolchain and
`rpc=True` / `tests=False` Conan options. The build stage upgrades Conan to the
newest 2.x release, as kth's own CI does: the pinned toolchain ships Conan
2.24.0, and kth 1.5.0 pulls `openssl/4.1.0`, whose recipe requires Conan 2.25 or
later. The package adds runtime utilities and the compatibility proxy; it does
not patch the Knuth node. Official GHCR pulls currently return an authorization
error, so packaging retains the source build.
The SDK is pinned to `@start9labs/start-sdk` 3.1.1.

---

## 2. Volume and Data Layout

| Volume Name | Mount Point | Purpose |
|---|---|---|
| `main` | `/data` | Config, store, and all chain data |

**StartOS-managed files:**

| Path | Managed By | Purpose |
|---|---|---|
| `kth.cfg` | `knuth.conf` file model | Node INI config (v1.3.0 key names: `net.*`, `db.*`, `chain.*`, `node.*`, `rpc.*`) |
| `store.json` | `store.json` file model | network, rpcEnabled, credentials, IPC/UTXOZ/Tor flags |

**Chain layout (same schema as BCHN/BCHD/Flowee):**

| Path | Network |
|---|---|
| `/data/blockchain` | mainnet chain DB |
| `/data/peers.dat` | mainnet hosts pool |
| `/data/testnet3`, `/data/testnet4`, `/data/scalenet`, `/data/chipnet`, `/data/regtest` | per-testnet chain + peers |

---

## 3. Installation and First-Run Flow

1. Seed `store.json` + `kth.cfg` (mainnet, RPC off, IPC/UTXOZ on, generated RPC creds).
2. Launch `kth -c /data/kth.cfg --init_run --network mainnet`.
3. Health: process liveness when RPC is off; native RPC + sync checks when RPC is on.
4. Enable JSON-RPC in Node Settings for mining pools / Fulcrum / Explorer backends.

**Upgrading from 1.3.x to 1.4.0 or later requires fresh chain data.** The UTXO-Z
database format changed in 1.4.0. Stop Knuth and run **Rebuild Blockchain
Database** for the active network before starting the new version, then allow a
full download and validation. The action preserves `kth.cfg`, network settings,
and RPC credentials. Repeat the rebuild before using any other network that still
has a 1.3.x database. A 1.4.0 database is reused by 1.5.0 without a rebuild. A
fresh installation starts with an empty database and does not need this action.

---

## 4. Default Networking

| Transport | Default | How to Change |
|---|---|---|
| **Clearnet** | Enabled | StartOS interfaces |
| **Tor** | Off | Node Settings + Tor package |
| **I2P** | Not implemented | — |

---

## 5. Configuration Management

| Group | Settings |
|---|---|
| **Network** | mainnet / testnet3 / testnet4 / scalenet / chipnet / regtest — rewrites peer port, RPC port, `db.directory`, `net.hosts_file` (the peer port, `db.directory` and `net.hosts_file` are also reasserted on every start, because kth tells chipnet from testnet4 only by peer port 48333) |
| **Node Settings** | db mode (`full` / `blocks` / `pruned`), max size (pruned), connections, latency, verbose log, **JSON-RPC toggle**, IPC, UTXOZ, Tor |

Interfaces are refreshed when the selected network, RPC toggle, or RPC username
changes. New RPC passwords use the operating system's cryptographic random
source. Existing credentials remain stable until the credential action rotates them.
Malformed or non-finite numeric config values use their declared defaults.

---

## 6. Network Access and Interfaces

| Interface | Port (mainnet) | Purpose | Condition |
|---|---|---|---|
| Peer | 8333 | P2P | Always |
| JSON-RPC | 8332 | API | When `rpcEnabled` |

Per-network peer/RPC ports match the shared BitcoinCash1 table (see Quick Reference).

---

## 7. Actions (StartOS UI)

| Action ID | Name | Group |
|---|---|---|
| `runtime-info` | Node Info | Info |
| `network-config` | Network | Configuration |
| `node-settings` | Node Settings | Configuration |
| `rpc-credentials` | RPC Credentials | Credentials |
| `delete-peer-list` | Delete Peer List | Maintenance (stopped only) |
| `delete-test-network-data` | Delete Test Network Data | Maintenance (stopped only; can wipe the active testnet) |
| `rebuild-chain-data` | Rebuild Blockchain Database | Maintenance (stopped only) |
| `autoconfig` | Auto-Configure | Hidden (cross-package) |

---

## 8. Backups and Restore

**Backed up:** `kth.cfg`, `store.json` (credentials, flags).

**Excluded:** chain dirs (`/blockchain`, testnet subdirs), `peers.dat`, logs.

---

## 9. Health Checks

| Check | When | Method |
|---|---|---|
| **RPC** (daemon ready) | Always | `getblockchaininfo` when JSON-RPC is on; otherwise the `kth` process |
| **Blockchain Sync** | Always | Native `getblockchaininfo` validated height/hash; any gap behind headers stays loading. On public networks, a current positive peer count and readable recent tip are also required. A tip older than 3 hours (24 hours on scalenet) stays loading. Regtest has no peer or tip-age requirement. With RPC off or unreadable, sync is unverifiable and stays loading. |
| **Peer Connections** | Always | Knuth `Peers: n/m` status log, accepted only when its timestamp is less than 2 minutes old; missing or stale status stays loading. |
| **Tor** | Always | Optional — listed as a dependency; health is disabled until Tor routing is turned on |
| **I2P** | Always | Disabled (same as BCHN/Flowee until implemented) |
| **Clearnet** | Always | Outbound unless a public address is published |
| **UTXO-Z Storage** / **IPC / C-API** | Always | Knuth-specific capability rows |

The compatibility proxy preserves the native validated chain tip; block files
cannot raise its reported height. Its network-info fallback uses the installed
binary version and current peer log, and returns an error when those facts are
unavailable. The stored `fullySynced` flag records a successful check and is not
proof of current connectivity or synchronization.

---

## 10. Dependencies

| Package | Optional | Purpose |
|---|---|---|
| `tor` | yes (optional) | Declared in the package manifest and activated as a running dependency when **Node Settings → Tor Routing** is enabled. The toggle advertises the integration; it does not configure an outbound SOCKS proxy in Knuth. |

---

## 11. Default Overrides

| Setting | StartOS value | Reason |
|---|---|---|
| `--init_run` | always | init chain then run |
| `--network` | from store | v1.3.0 network select |
| `db.db_mode` | `full` | indexed; required for Fulcrum/Explorer |
| `rpc.enabled` | false | opt-in; credentials pre-generated |
| `rpc.bind` | `127.0.0.1` | native RPC stays internal on 19332; the compatibility proxy exposes the public per-network RPC port |
| `net.hosts_file` | under `/data` | kth defaults to CWD `/` |
| IPC / UTXOZ | true | capability flags for dependents |

---

## 12. Limitations and Differences

1. **Built from source.** The `Dockerfile` builds the pinned release using the merged upstream recipe. Release checks try the official image first and build the package image when it is unavailable. Packaging itself retains the source Dockerfile. `scripts/use-upstream-image.sh` and `scripts/Dockerfile.upstream` are legacy manual helpers, not part of the daily workflow; there is no automatic wrapper switch.
2. **Synchronization requires JSON-RPC.** Native validated height/hash, fresh peer status, and tip time determine health. Raw block files and old log heights do not establish validation progress.
3. **gRPC not exposed** in this package.
4. The Tor toggle declares the optional dependency but does not configure outbound SOCKS routing; outbound peers use clearnet.
5. **RPC compatibility sidecar** — the pinned release still has native RPC gaps. The package runs `scripts/rpc_compat.py` on the public RPC port and leaves kth on `127.0.0.1:19332`. It serves block/transaction data from block files and adapts classic mining methods to `getblocktemplatelight` / `submitblocklight`. JSON-RPC bodies end with a trailing LF for line-oriented pool clients. These adapters do not establish tested compatibility with every miner, Fulcrum, or explorer; dependent-service integration must be exercised on a running StartOS installation.
6. **Database compatibility.** Knuth 1.4.0 and later cannot reuse a 1.3.x UTXO-Z database. Rebuild before starting them; automatic version builds do not make future database formats compatible.

---

## 13. What Is Unchanged from Upstream

- Consensus / P2P protocol from `k-nuth/kth`
- Transaction relay and size policy from the pinned upstream release; this package adds no size cap or consensus rule
- Config key names and `db_mode` values from v1.3.0
- IPC/C-API and UTXOZ behavior

---

## 14. Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md)

`check-upstream.yml` checks the latest stable `k-nuth/kth` release daily on
`master`. Before editing pins, `scripts/auto-bump.sh` checks the requested binary
version and starts an isolated regtest JSON-RPC smoke test. It tries the official
image first and builds from the package's upstream recipe if the image cannot be
pulled. A failed check leaves the existing pins intact. Older releases never
downgrade the package.

After validation, the updater sets `ARG KNUTH_VERSION` and
`startos/versions/current.ts` to the new upstream release at revision `:0`, resets
`ALLOW_DOWNGRADE` to `false`, commits to `master`, and explicitly dispatches Tag
and Release. A later daily run retries a missing package release. Tag and Release
checks the binary/RPC again, builds the signed packages with the newest
`start-cli` (installed by `Start9Labs/start-technologies`' build environment), and
publishes the GitHub release without moving an existing tag to another commit. A
push or dispatch whose version already has a release ends without building, so
docs and CI changes on `master` do not fail it, and a dispatch that arrives during
a build waits instead of cancelling it. Package-only revisions remain a maintainer
decision. The schedule uses the workflow on `master`; PR changes do not activate
it before merge.

For a sideload artifact from a branch, run **Actions → Build → Run workflow** and
select the branch and target (`x86`, `arm`, `riscv`, or `all`). The manual job also
runs for draft PR branches and uploads `.s9pk` files with `SHA256SUMS`; it does not
publish a release. A successful package build is not live sync or mining proof.

---

## 15. Quick Reference for AI Consumers

```yaml
package_id: knuth-bch
upstream_repo: https://github.com/k-nuth/kth
package_repo: https://github.com/BitcoinCash1/knuth-bch-startos
command: [kth, -c, /data/kth.cfg, --init_run, --network, <name>]
networks:
  mainnet:  { peer: 8333,  rpc: 8332,  db: /data/blockchain, hosts: /data/peers.dat }
  testnet3: { peer: 18333, rpc: 18332, db: /data/testnet3 }
  testnet4: { peer: 28333, rpc: 28332, db: /data/testnet4 }
  scalenet: { peer: 38333, rpc: 38332, db: /data/scalenet }
  chipnet:  { peer: 48333, rpc: 48332, db: /data/chipnet }
  regtest:  { peer: 18444, rpc: 18443, db: /data/regtest }
config_keys_v1_3_0: [net.*, db.*, chain.*, node.*, rpc.*, log.*]
db_mode: [full, blocks, pruned]   # not full_indexed/normal
rpc_available: true               # when image built with rpc=True and toggle on
schema_reference: bitcoin-cash-node-startos
```
