<div align="center">
  <img src="icon.png" alt="Knuth logo" width="21%" />
  <h1>Knuth</h1>
</div>

> **Upstream docs:** [github.com/k-nuth/kth](https://github.com/k-nuth/kth) · [kth.cash](https://kth.cash)
>
> Knuth is a high-performance Bitcoin Cash full node written in C++. It validates blocks, relays transactions, and syncs the BCH blockchain. This package exposes node settings including database mode (full indexed, normal, or pruned), IPC/C-API capabilities, and UTXOZ compatibility. JSON-RPC is not yet available in this release.

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
| **Build** | Docker build from `Dockerfile.binary` (builds from `github.com/k-nuth/kth` source) |
| **Architectures** | `x86_64`, `aarch64`, `riscv64` |
| **Command** | `kth -c /data/kth.cfg --init_run [--testnet | --testnet4 | ...]` |

---

## 2. Volume and Data Layout

| Volume Name | Mount Point | Purpose |
|---|---|---|
| `main` | `/data` | All node data: blockchain, chainstate, database, configuration |

**StartOS-managed files inside `/data`:**

| File / Directory | Managed By | Purpose |
|---|---|---|
| `kth.cfg` | StartOS SDK file model (`knuth.conf`) | Knuth node configuration (INI-style key-value) |
| `store.json` | StartOS SDK file model | Package state: network, database mode, IPC flag, UTXOZ flag, Tor flag |
| `blockchain/` | Knuth | Blockchain database |
| `blocks/` | Knuth | Raw block data |
| `chainstate/` | Knuth | UTXO set |
| `db/` | Knuth | Additional database files |
| `peers.json` | Knuth | Cached peer addresses |

---

## 3. Installation and First-Run Flow

1. StartOS builds the `knuth` container image from source.
2. Seed files are written: `kth.cfg` and `store.json` with defaults (network: mainnet, database mode: full_indexed, IPC enabled, UTXOZ enabled, Tor disabled).
3. Knuth launches with `--init_run`, which initializes the chain database if it does not exist, then begins normal operation.
4. The node connects to the BCH mainnet P2P network and begins Initial Block Download (IBD).
5. The Node ready health check confirms the `blockchain/` directory exists; no RPC probe is performed (Knuth has no RPC in this version).
6. IBD proceeds silently — there is no block count health check in this version (no RPC to query progress).

---

## 4. Default Networking

| Transport | Default | Inbound | How to Change |
|---|---|---|---|
| **Clearnet (IPv4/IPv6)** | Enabled — P2P port exposed by StartOS | Enabled when StartOS assigns an external IP | Automatic via StartOS |
| **Tor** | Disabled by default | Not configured in this version | Enable in Node Settings action; requires Tor package |
| **I2P** | Not implemented | Not available | Not available |

---

## 5. Configuration Management

| Group | Settings Covered |
|---|---|
| **Network** | Network selection: mainnet, testnet3, testnet4, scalenet, chipnet, regtest — P2P port adjusts automatically |
| **Node Settings** | Database mode (full_indexed / normal / pruned), max database size (GB, pruned mode only), outbound connections, inbound connections, block latency, verbose logging, IPC enabled, UTXOZ enabled, Tor routing toggle |

---

## 6. Network Access and Interfaces

| Interface | Port | Protocol | Purpose | Condition |
|---|---|---|---|---|
| Peer Interface | 8333 | TCP | P2P Bitcoin Cash network | Always — mainnet |
| Peer (testnet3) | 18333 | TCP | Testnet3 P2P | When network = testnet3 |
| Peer (testnet4) | 28333 | TCP | Testnet4 P2P | When network = testnet4 |
| Peer (scalenet) | 38333 | TCP | Scalenet P2P | When network = scalenet |
| Peer (chipnet) | 48333 | TCP | Chipnet P2P | When network = chipnet |
| Peer (regtest) | 18444 | TCP | Regtest P2P | When network = regtest |

**Note:** There is no RPC interface. Knuth v0.83.0 exposes P2P only. JSON-RPC and gRPC are planned for a future upstream release.

---

## 7. Actions (StartOS UI)

### Info

| Action ID | Name | Description |
|---|---|---|
| `runtime-info` | Node Info | Displays network, P2P port, blockchain directory presence, and block segment count |

### Configuration

| Action ID | Name | Description |
|---|---|---|
| `network-config` | Network | Select BCH network; P2P port adjusts automatically on restart |
| `node-settings` | Node Settings | Database mode, max DB size (pruned), connection limits, block latency, verbose logging, IPC, UTXOZ, Tor routing |

### Credentials

| Action ID | Name | Description |
|---|---|---|
| `rpc-credentials` | RPC Credentials | Placeholder — **disabled**. Message: "RPC support is coming in a future release." |

### Maintenance

| Action ID | Name | Description |
|---|---|---|
| `delete-peer-list` | Delete Peer List | Remove cached `peers.json`; Knuth rebuilds peer discovery on next start |
| `delete-test-network-data` | Delete Test Network Data | Wipe data directory for the currently selected test network |

### Hidden (cross-package)

| Action ID | Name | Description |
|---|---|---|
| `autoconfig` | Auto-Configure | Cross-package action; currently returns no RPC credentials (Knuth has no RPC) |

---

## 8. Backups and Restore

**What IS backed up:**
- `kth.cfg` — node configuration
- `store.json` — network, database mode, flags

**What is NOT backed up:**
- `/blocks` — raw blockchain data (re-downloaded after restore)
- `/chainstate` — UTXO set (derived from blocks)
- `/db` — additional database files (derived from blocks)
- `/peers.json` — peer address cache (rebuilt on connect)

Restoring overwrites current configuration. Blockchain data must be re-synced from genesis after restore.

---

## 9. Health Checks

| Check | Method | Key Messages |
|---|---|---|
| **Node** (daemon ready) | `test -d /data/blockchain` — checks blockchain directory exists | `Knuth node is running` / `Knuth is initializing...` / `Knuth is starting...` |
| **Tor** | Store flags + Tor package state | `All connections routed through Tor` / `Tor routing is disabled in config` / `Tor is not installed` / `Tor is not running` |
| **Clearnet** | Tor mode state | `Direct clearnet connections` / `Outbound via Tor proxy — clearnet peers still reachable` |

---

## 10. Dependencies

### Tor (optional)

| Field | Value |
|---|---|
| **Package ID** | `tor` |
| **Version constraint** | Any |
| **Required state** | Running (optional — used only when Tor is enabled in Node Settings) |
| **Health checks** | Container IP via `sdk.getContainerIp`; running state via `sdk.getStatus` |
| **Mounted volumes** | None |
| **Purpose** | Provides SOCKS5 proxy for Tor-routed P2P connections. Note: Tor routing for Knuth is configured but the proxy passthrough to Knuth's `kth` binary is in development — verify behavior after enabling. |

---

## 11. Default Overrides

| Setting | Upstream Default | StartOS Value | Reason |
|---|---|---|---|
| `--init_run` | Not default | Always passed | Initializes the chain database on first run, then starts the node — avoids a separate init step |
| Database mode | Varies | `full_indexed` default | Full indexed mode provides maximum data availability for dependent packages and future RPC use |
| IPC enabled | Varies | `true` | IPC/C-API is Knuth's primary extensibility mechanism; enabled for future integrations |
| UTXOZ enabled | Varies | `true` | UTXOZ compatibility enabled for tools that consume Knuth's extended UTXO format |
| Tor routing | Off | Off by default | Unlike BCHD and Flowee, Tor is opt-in for Knuth; no proxy activation deferral logic is implemented |
| `node.ds_proofs_enabled` / `node.ds_proofs` | Varies | `true` (both) | Always-on; Double Spend Proof relay is always enabled |
| `network.relay_transactions` | Varies | `true` | Always-on; transaction relay is always active |
| `node.compact_blocks_high_bandwidth` | Varies | `true` | Always-on; high-bandwidth compact block mode for faster propagation |

---

## 12. Limitations and Differences

1. **No JSON-RPC in this version.** Knuth v0.83.0 is a P2P-only node on StartOS. RPC (JSON-RPC and/or gRPC) will be added in a future upstream release. The RPC Credentials action is present but disabled.
2. **No blockchain sync progress health check.** Because there is no RPC, the sync health check only confirms the `blockchain/` directory exists. There is no percentage or block count display during IBD.
3. **Cannot be used as a backend** for Fulcrum, BCH Explorer, ASICSeer, or EloPool in this version. These packages require RPC connectivity. When selected as a backend in Fulcrum, the connection will fail until RPC is available.
4. Three database modes are available: `full_indexed` (all data, maximum storage), `normal` (standard storage), and `pruned` (minimal storage with configurable max size). Changing database mode requires a full re-sync.
5. **Tor routing is opt-in** and disabled by default. Unlike BCHD and Flowee, there is no automatic Tor proxy deferral during IBD.
6. The Node Info action shows filesystem-level information only (directory presence, block segment count) — not RPC-derived chain info.

---

## 13. What Is Unchanged from Upstream

- All Bitcoin Cash consensus rules and network protocols implemented in `k-nuth/kth`
- P2P peer connection behavior and protocol
- Database format and storage layout for all three modes (full_indexed, normal, pruned)
- IPC/C-API interface behavior
- UTXOZ compatibility

---

## 14. Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 15. Quick Reference for AI Consumers

```yaml
package_id: knuth-bch
title: Knuth
license: MIT
upstream_repo: https://github.com/k-nuth/kth
package_repo: https://github.com/BitcoinCash1/knuth-bch-startos
image:
  id: knuth
  build: dockerfile
  source: Dockerfile.binary (builds from k-nuth/kth source)
architectures:
  - x86_64
  - aarch64
  - riscv64
volumes:
  - name: main
    mountpoint: /data
    purpose: blockchain data, config, database
ports:
  - interface: peer
    port: 8333
    protocol: tcp
    purpose: P2P Bitcoin Cash network
    condition: always (mainnet)
  # No RPC port — JSON-RPC not available in v0.83.0
networks_supported:
  mainnet:  { peer: 8333 }
  testnet3: { peer: 18333 }
  testnet4: { peer: 28333 }
  scalenet: { peer: 38333 }
  chipnet:  { peer: 48333 }
  regtest:  { peer: 18444 }
dependencies:
  tor:
    optional: true
    purpose: SOCKS5 proxy for Tor-routed P2P connections (opt-in, disabled by default)
startos_managed_files:
  - /data/kth.cfg
  - /data/store.json
actions:
  - { id: runtime-info, name: "Node Info", group: Info }
  - { id: network-config, name: "Network", group: Configuration }
  - { id: node-settings, name: "Node Settings", group: Configuration }
  - { id: rpc-credentials, name: "RPC Credentials", group: Credentials, status: disabled }
  - { id: delete-peer-list, name: "Delete Peer List", group: Maintenance }
  - { id: delete-test-network-data, name: "Delete Test Network Data", group: Maintenance }
  - { id: autoconfig, name: "Auto-Configure", group: hidden }
health_checks:
  - { id: primary, display: "Node", method: "test -d /data/blockchain" }
  - { id: tor, display: "Tor", method: "store flags + Tor package status" }
  - { id: clearnet, display: "Clearnet", method: "tor mode state" }
rpc_available: false
rpc_planned: true
database_modes: [full_indexed, normal, pruned]
backup_volumes:
  - main
backup_excludes:
  - /blocks
  - /chainstate
  - /db
  - /peers.json
```
