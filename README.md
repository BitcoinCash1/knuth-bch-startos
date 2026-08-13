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
| **Build** | `Dockerfile` copies `kth` + GCC 15 runtime from upstream `ghcr.io/k-nuth/kth` (or a local RPC-enabled build until upstream ships `rpc=True`) |
| **Architectures** | `x86_64` (upstream currently ships amd64; others emulate) |
| **Command** | `kth -c /data/kth.cfg --init_run --network <name>` |

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
3. Health: chaindir liveness when RPC is off; RPC + sync checks when RPC is on.
4. Enable JSON-RPC in Node Settings for mining pools / Fulcrum / Explorer backends.

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
| **Network** | mainnet / testnet3 / testnet4 / scalenet / chipnet / regtest — rewrites peer port, RPC port, `db.directory`, `net.hosts_file` |
| **Node Settings** | db mode (`full` / `blocks` / `pruned`), max size (pruned), connections, latency, verbose log, **JSON-RPC toggle**, IPC, UTXOZ, Tor |

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
| **Blockchain Sync** | Always | Knuth `Fully synced at height` / `Stats:` log, merged with RPC (RPC `blocks` often stays 0) |
| **Peer Connections** | Always | Knuth `Peers: n/m` status log (no `getpeerinfo` in v1.3.0) |
| **Tor** | Always | Optional — listed as a dependency; health is disabled until Tor routing is turned on |
| **I2P** | Always | Disabled (same as BCHN/Flowee until implemented) |
| **Clearnet** | Always | Outbound unless a public address is published |
| **UTXO-Z Storage** / **IPC / C-API** | Always | Knuth-specific capability rows |

---

## 10. Dependencies

| Package | Optional | Purpose |
|---|---|---|
| `tor` | yes (optional) | Always listed on the Dependencies tab (`kind: exists` when Tor Routing is off, `kind: running` when on). Not required to be running unless **Node Settings → Tor Routing** is enabled. |

---

## 11. Default Overrides

| Setting | StartOS value | Reason |
|---|---|---|
| `--init_run` | always | init chain then run |
| `--network` | from store | v1.3.0 network select |
| `db.db_mode` | `full` | indexed; required for Fulcrum/Explorer |
| `rpc.enabled` | false | opt-in; credentials pre-generated |
| `rpc.bind` | `0.0.0.0` | reachable from other containers |
| `net.hosts_file` | under `/data` | kth defaults to CWD `/` |
| IPC / UTXOZ | true | capability flags for dependents |

---

## 12. Limitations and Differences

1. **Official `ghcr.io/k-nuth/kth` may lack RPC** until upstream builds with `rpc=True` (see `k-nuth/docker-images` PR #7). This package can use a local RPC-enabled image interim.
2. **No `initialblockdownload` / `verificationprogress`** — sync health uses Knuth's coordinator log (RPC `getblockchaininfo.blocks` is often 0 at the tip).
3. **gRPC not exposed** in this package.
4. Tor proxy passthrough to `kth` is opt-in; verify after enabling.

---

## 13. What Is Unchanged from Upstream

- Consensus / P2P protocol from `k-nuth/kth`
- Config key names and `db_mode` values from v1.3.0
- IPC/C-API and UTXOZ behavior

---

## 14. Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md)

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
