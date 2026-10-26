# Knuth

## Documentation

- [Knuth on GitHub](https://github.com/k-nuth/kth) — the upstream project, its releases, and its README.
- [JSON-RPC reference](https://github.com/k-nuth/kth/blob/master/docs/json-rpc.md) — every method the node answers, including the mining calls.

## What you get on StartOS

A Bitcoin Cash full node that validates blocks, relays transactions, and syncs the chain. It exposes a **Peer Interface** for the Bitcoin Cash network, and — once you turn it on — a **JSON-RPC Interface** other services can call.

Knuth can run on mainnet or on any of five test networks, and each keeps its own copy of the chain. Switching between them is a setting, not a reinstall, and the network you leave keeps its data for when you come back.

## Getting set up

1. Start Knuth. It begins syncing mainnet straight away — expect the initial sync to take a long time and a large amount of disk.
2. Watch **Blockchain Sync** and **Peer Connections** on the service page to confirm it is making progress.

That is all that is required. Everything below is optional.

### Choosing a different network

Run **Network** and pick one. Knuth restarts, and its peer and JSON-RPC ports change to match the network you chose. If you have never run that network before, it syncs from the beginning.

### Turning on JSON-RPC

Other services — a mining pool, an indexer like Fulcrum, a block explorer — connect to Knuth over JSON-RPC. It is off until you enable it.

1. Run **Node Settings** and turn on **JSON-RPC Server**. Knuth restarts and a **JSON-RPC Interface** appears alongside the Peer Interface.
2. Run **RPC Credentials** to see the username and password to give that service.

Your credentials are created when Knuth is installed and never change on their own, so a service you configure once keeps working. **Generate RPC Credentials** replaces them if you need to — Knuth only supports one username and password, so everything using the old one stops working until you update it.

**Leave Database Mode on Full Indexed** if you want Fulcrum or a block explorer to work. The other modes do not build the transaction index those services need, and switching back means syncing the chain again from the start.

### Tor integration

Install the Tor package, then turn on **Tor Routing** in **Node Settings** to activate the optional dependency and its health check. This setting does not configure an outbound SOCKS proxy in Knuth. To accept inbound connections over Tor, add an onion address to the **Peer Interface**; that is configured independently of the toggle.

## Maintenance

Knuth has to be stopped before any of these will run, and none of them can be undone.

- **Delete Peer List** — throws away everything Knuth knows about other nodes, including any it has banned, and rediscovers from scratch. This is the fix when Knuth sits at zero peers, which usually means it kept the banned peers from a network you were previously on.
- **Delete Test Network Data** — frees the disk a test network is using. Mainnet cannot be selected and is never affected.
- **Rebuild Blockchain Database** — deletes the chain for the network you are currently on so Knuth downloads it again. Only reach for this if the database is genuinely corrupted; on mainnet it means hours of resyncing. Your peer list and JSON-RPC credentials are kept.

**Node Info** is safe to run at any time while Knuth is running, and just reports the current network and whether chain data is present.

## Limitations

- **Only 64-bit x86 has a native image.** ARM and RISC-V hardware use StartOS emulation of that image. Practical mainnet sync on those architectures has not been verified.
- **Your backup does not include the chain.** It keeps your settings and your JSON-RPC credentials, so a restored Knuth is configured correctly — but it has to sync the chain again from the beginning before it is useful.
- **Knuth does not support I2P.** The I2P row on the service page is always shown as unavailable.
