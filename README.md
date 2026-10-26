<p align="center">
  <img src="icon.svg" alt="Knuth Logo" width="21%" />
</p>

# Knuth on StartOS

> Everything not listed in this document should behave the same as upstream
> Knuth. If a feature, setting, or behavior is not mentioned here, the
> upstream documentation is accurate and fully applicable — see the
> Documentation section of `instructions.md` for links.

[Knuth](https://github.com/k-nuth/kth) is a Bitcoin Cash full node written in C++. Two things about this package are not upstream behavior: it is **built from source with JSON-RPC enabled**, and the JSON-RPC port a dependent service connects to is served by a **compatibility sidecar**, not by kth directly.

- **Upstream repo:** <https://github.com/k-nuth/kth>
- **Wrapper repo:** <https://github.com/Start9-Community/knuth-bch-startos>

---

## Table of Contents

- [Image and Container Runtime](#image-and-container-runtime)
- [Volume and Data Layout](#volume-and-data-layout)
- [File Models](#file-models)
- [Dependencies](#dependencies)
- [Network Access and Interfaces](#network-access-and-interfaces)
- [Installation and First-Run Flow](#installation-and-first-run-flow)
- [Actions](#actions)
- [Tasks](#tasks)
- [Health Checks](#health-checks)
- [Backups and Restore](#backups-and-restore)
- [Limitations and Differences](#limitations-and-differences)
- [Quick Reference for AI Consumers](#quick-reference-for-ai-consumers)

---

## Image and Container Runtime

One image, built here from upstream source rather than pulled.

| Property      | Value                                                  |
| ------------- | ------------------------------------------------------ |
| Image         | built from `Dockerfile` (kth compiled with `rpc=True`) |
| Architectures | x86_64 native; other architectures use StartOS emulation |
| Command       | `kth -c /data/kth.cfg --init_run --network <name>`     |

| Subcontainer | Purpose                                                                                                             |
| ------------ | ------------------------------------------------------------------------------------------------------------------- |
| `knuth-sub`  | Shared by the `primary` and `rpc-compat` daemons and by every action's temporary container — the one to `attach` to |

The package builds its pinned release from source with the `rpc` Conan option enabled. The toolchain image is amd64-only. The SDK manifest therefore declares `arch: ['x86_64']` and `emulateMissing: true`: ARM and RISC-V installations use emulation rather than native builds. Their practical mainnet sync performance has not been verified.

The package uses the current SDK dependency classes and manifest builder. The read-only validation workflow checks TypeScript, the delivered numeric configuration schema, and the package bundle on pull requests, including drafts; it does not publish a package or replace installation testing.

The runtime image also carries `curl` and `python3` — `curl` because kth ships no RPC client to health-check with, `python3` for the compatibility sidecar below.

## Volume and Data Layout

One volume, laid out per network so switching networks never mixes chain state or peer bans.

| Path                           | Contents                                         |
| ------------------------------ | ------------------------------------------------ |
| `/data/kth.cfg`                | kth's configuration file                         |
| `/data/store.json`             | This package's own state                         |
| `/data/blockchain/`            | Mainnet chain data, UTXO-Z database, block store |
| `/data/<network>/`             | The same, per test network                       |
| `/data/peers.dat`              | Mainnet peer and ban list                        |
| `/data/<network>/peers.dat`    | The same, per test network                       |
| `/data/debug.log`, `error.log` | kth's own logs                                   |

Both `net.hosts_file` and `db.directory` are written explicitly on every network change, because kth's own defaults put the chain in one place regardless of network and write `peers.dat` to the process working directory — outside the volume entirely.

## File Models

Two, and they hold different kinds of state.

| Model       | File               | Seeded by              | Rewritten by                                                                                                                         |
| ----------- | ------------------ | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `knuthConf` | `/data/kth.cfg`    | `seedFiles` at install | **Node Settings**, **Network**, **Mempool & Block Policy**, **RPC & Peers Settings**, **Auto-Configure**, and the credential actions |
| `storeJson` | `/data/store.json` | `seedFiles` at install | **Node Settings**, **Network**, **Auto-Configure**, the credential actions, and the `synced-true` oneshot                            |

`kth.cfg` declares far more keys than any action exposes. The `[fork]` group and the protocol service bits are declared so that a hand-edited file **round-trips** through `merge` instead of being dropped — they are deliberately not offered in the UI, because overriding a consensus parameter forks the node off the real chain.

**A hand edit survives, with two exceptions.** `rpc.bind` and `rpc.port` are re-asserted on every start when the JSON-RPC server is on, because kth must stay bound to loopback for the sidecar arrangement to hold; and `net.inbound_port`, `net.hosts_file`, and `db.directory` are rewritten whenever the network changes. Everything else is yours once set.

**A misspelled key fails silently.** kth calls `allow_config_extras()`, so an unrecognised key is ignored and the node runs on its own default with no warning. An _invalid value_ for a recognised key does the opposite and aborts startup with "illegal value" — which is why `log.statistics_server` and `net.self` are omitted rather than written blank, and why the 1.3.0 `db.db_mode` value rename needed a migration.

Numeric configuration values are validated after conversion. Non-numeric strings, `NaN`, and infinities are rejected instead of being carried into the generated INI file; fields with declared defaults use those defaults.

## Dependencies

One optional dependency, activated by the Tor Routing setting.

| Dependency | Kind                                          | Why                                |
| ---------- | --------------------------------------------- | ---------------------------------- |
| `tor`      | Optional; `running` when Tor Routing is on | Tor package integration |

The dependency is declared through the SDK dependency class. Enabling the setting requires Tor to be running; it does not configure an outbound SOCKS proxy in kth. Inbound onion access is configured through StartOS Interfaces.

## Network Access and Interfaces

kth listens on exactly two ports. The peer interface is always exported; the JSON-RPC one appears only when the JSON-RPC server is turned on.

| Interface          | Id     | Type | Port                       | Description                                          |
| ------------------ | ------ | ---- | -------------------------- | ---------------------------------------------------- |
| Peer Interface     | `peer` | p2p  | per network (8333 mainnet) | Peer-to-peer connections on the Bitcoin Cash network |
| JSON-RPC Interface | `rpc`  | api  | per network (8332 mainnet) | Bitcoin-Cash-compatible JSON-RPC, masked             |

**The exported JSON-RPC port is not kth's.** kth itself is pinned to `127.0.0.1:19332`, and the exported port is served by `rpc_compat.py`, a Python sidecar in the same container that proxies to kth and fills in what kth cannot answer. kth 1.3.0's `fetch_block()` is a stub, so `getblock` and `getrawtransaction` return nothing and `getnetworkinfo` is absent — a dependent pointed straight at kth fails in ways that look like a broken node. The sidecar reads the block store on disk to answer those, and passes everything else through.

Both interfaces re-derive when the network changes or the JSON-RPC server is toggled, so the ports follow the selected network without waiting for a package update.

## Installation and First-Run Flow

Nothing to configure, and no prompt. `seedFiles` runs once at install: it writes `kth.cfg` with mainnet's ports and paths, generates a JSON-RPC username and a cryptographically random password, and leaves the JSON-RPC server **off**. The node starts on mainnet and begins syncing immediately.

Nothing in this package raises a task, so the ordinary controls are available from the moment it installs.

The JSON-RPC credential is generated at install and then left alone — updates never rotate it, so a dependent configured once keeps working.

## Actions

Ten user-facing actions in four groups, plus one hidden. What follows is what the OS metadata cannot carry.

### Configuration

**Node Settings**, **Mempool & Block Policy**, and **RPC & Peers Settings** all write `kth.cfg` and restart the node — a few seconds' interruption, and idempotent. They are split the way BCHN splits them so the three BCH node packages read alike.

Two Node Settings fields cost more than a restart. **Database Mode** decides whether a transaction index exists at all: leaving Full Indexed makes Fulcrum and BCH Explorer unable to connect, and coming back requires a full resync, because kth has no in-place reindex. **Maximum Database Size** applies only in Pruned mode and is ignored otherwise.

**Network** additionally rewrites the chain directory, the hosts file, and the peer port, then restarts. Switching to a network with no data on disk starts a sync from genesis for that network; the network you left keeps its data and comes back untouched if you switch again. It is a no-op if the selected network is already active, and says so.

### Credentials

**Generate RPC Credentials** rotates the single password kth supports — there is no second credential to fall back on, so every dependent breaks until it is updated. **Delete RPC Credentials** clears both fields; kth then falls back to a generated cookie file, so the node keeps working and only shared-password clients lose access. **RPC Credentials** is read-only and reports that the server is off rather than showing a credential that nothing is listening for.

All three restart the node.

### Maintenance

All three require the service to be **stopped**, and none can be undone.

**Delete Peer List** removes the hosts file, including bans. Cheap; the node rebuilds from DNS seeds in minutes. Reach for it when the node sits at zero peers.

**Delete Test Network Data** frees the disk a test network is using. Mainnet is not selectable. Selecting the active network also clears the synced flag.

**Rebuild Blockchain Database** deletes the chain, UTXO-Z, and block store for the **active** network. This is a full re-download — hours on mainnet — and exists because kth has no in-place reindex. It keeps the peer list and the JSON-RPC credentials. Use it for a corrupted database, not as a first troubleshooting step.

### Other

**Node Info** is read-only and requires the service to be running. It reports the network, the peer port, and whether chain data exists.

### Hidden

**Auto-Configure** (`autoconfig`) is invoked by another package, not by a person; a support agent should never tell a user to run it. It applies only the fields the caller supplied and leaves the rest alone.

## Tasks

None. This package raises no tasks, so the service is never held on a prompt and its ordinary controls are always available.

## Health Checks

Nine rows, and only four of them probe anything. Reading them as if they were all liveness checks is the main way to misdiagnose this package.

| Check              | Displayed         | What it actually does                                                                            |
| ------------------ | ----------------- | ------------------------------------------------------------------------------------------------ |
| `primary`          | RPC               | Calls `getblockchaininfo` on kth's loopback port; with JSON-RPC off, checks the process is alive |
| `rpc-compat`       | RPC Compatibility | Expects 200 or 401 from the sidecar; reports disabled when JSON-RPC is off                       |
| `sync-progress`    | Blockchain Sync   | Chain height, from the sidecar when available and `debug.log` otherwise                          |
| `peer-connections` | Peer Connections  | Peer count parsed out of `debug.log`                                                             |
| `tor`              | Tor               | Whether the Tor package is installed and running                                                 |
| `utxoz`            | UTXO-Z Storage    | Whether the UTXO-Z directory exists                                                              |
| `i2p`              | I2P               | Constant — kth has no I2P support                                                                |
| `clearnet`         | Clearnet          | Constant                                                                                         |
| `ipc-capi`         | IPC / C-API       | Reports a stored flag                                                                            |

**Blockchain Sync prefers the sidecar's height over kth's own.** kth's `blocks` field routinely trails `headers` by a few at the tip, which read naively renders as "Syncing 100.00%" forever; a gap under 0.1% is therefore treated as synced. Its fallback path parses `debug.log`, which matters during early sync when RPC reports genesis while the coordinator log already shows real progress.

**Peer Connections is log-derived**, so it reports "no peers" for the first minute of a start simply because nothing has been logged yet, and it will stay wrong if upstream changes that log line's format. Persistent zero after a few minutes is a real symptom — usually a peer list carrying bans from a different network, which **Delete Peer List** fixes.

The last three rows are **advertisements, not probes**: `i2p` is always disabled, `clearnet` always succeeds, and `ipc-capi` reflects a toggle. They exist so a dependent service and its user can see which capabilities this node claims. Do not read a green `ipc-capi` as evidence that anything was tested.

## Backups and Restore

The `main` volume is copied wholesale, minus the chain — `sdk.Backups.ofVolumes('main')` with every network's data directory, both `peers.dat` locations, and the two log files excluded.

So a backup holds `kth.cfg` and `store.json`: the configuration, the network selection, and the JSON-RPC credentials. That keeps the archive small and keeps dependents working after a restore, since the credential they were configured with comes back. **A restored node syncs the chain from genesis** — that is the trade, and on mainnet it is a multi-hour operation before the node is useful again.

## Limitations and Differences

1. **The exported JSON-RPC port is a compatibility sidecar, not kth.** It answers `getblock`, `getrawtransaction`, and `getblocktemplate` from the block store on disk, because kth 1.3.0's `fetch_block()` is a stub. Anything the sidecar does not implement is proxied to kth, and anything kth does not implement is absent.
2. **Only x86_64 is native.** ARM and RISC-V installations use StartOS emulation of the amd64 image. Native builds and practical mainnet sync on those architectures remain unverified.
3. **No I2P.** kth has no I2P support; the health row is a placeholder.
4. **The Tor setting activates an optional dependency.** kth exposes no SOCKS or onion command-line options, so the toggle surfaces Tor's health rather than configuring an outbound proxy. Inbound Tor is a StartOS onion address on the peer interface, configured independently of the toggle.
5. **One JSON-RPC credential.** kth has a single `rpc.user`/`rpc.password` pair, not BCHN's multiple `rpcauth` entries, so rotating it breaks every dependent at once.
6. **No in-place reindex.** kth's only chain-init flag builds a fresh database, so the repair action re-downloads rather than re-verifying.
7. **The chain is not backed up.** Restoring gives you a configured node that must sync from genesis.

---

## Quick Reference for AI Consumers

```yaml
package_id: knuth-bch
image: built from Dockerfile # k-nuth/kth compiled with rpc=True
architectures:
  - x86_64 # native
emulate_missing: true # other architectures emulate the x86_64 image
subcontainers:
  - knuth-sub # shared by both daemons and every action
volumes:
  main: /data
file_models:
  - /data/kth.cfg
  - /data/store.json
startos_managed_env_vars: []
dependencies:
  - tor # optional; activated as running when Tor Routing is on
interfaces:
  peer: { type: p2p, port: 8333 } # mainnet; per-network
  rpc: { type: api, port: 8332 } # mainnet; per-network, only when JSON-RPC is on
actions:
  - node-settings
  - network-config
  - mempool-policy
  - rpc-peers-settings
  - generate-rpc-credentials
  - delete-rpc-credentials
  - rpc-credentials
  - delete-peer-list
  - delete-test-network-data
  - rebuild-chain-data
  - runtime-info
  - autoconfig # hidden; called by another package
tasks: []
health_checks:
  - primary # displayed "RPC"
  - rpc-compat # displayed "RPC Compatibility"
  - sync-progress # displayed "Blockchain Sync"
  - peer-connections # displayed "Peer Connections"
  - tor
  - utxoz # capability advertisement
  - i2p # capability advertisement, always disabled
  - clearnet # capability advertisement, always success
  - ipc-capi # capability advertisement
```
