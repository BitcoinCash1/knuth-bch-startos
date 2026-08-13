import { sdk } from './sdk'
import {
  rootDir,
  networkPorts,
  networkName,
  networkDbDir,
  Network,
} from './utils'
import { storeJson } from './file-models/store.json'
import { mainMounts } from './mounts'

export { mainMounts }

export const main = sdk.setupMain(async ({ effects }) => {
  console.log('Starting Knuth!')

  const store = await storeJson.read().once()
  const network: Network = store?.network ?? 'mainnet'
  const { rpc: rpcPort } = networkPorts[network]
  const netName = networkName[network]
  const netLabel = network.charAt(0).toUpperCase() + network.slice(1)
  const dataDir = networkDbDir(network)
  const torEnabled = store?.torEnabled ?? false
  const rpcEnabled = store?.rpcEnabled ?? false
  const rpcUser = store?.rpcUser ?? ''
  const rpcPassword = store?.rpcPassword ?? ''
  const utxozEnabled = store?.utxozEnabled ?? true
  const ipcEnabled = store?.ipcEnabled ?? true

  // Tor — get container IP (same pattern as BCHN/BCHD/Flowee)
  const torIp = torEnabled
    ? await sdk.getContainerIp(effects, { packageId: 'tor' }).const()
    : null

  let torRunning = false
  if (torIp) {
    sdk.getStatus(effects, { packageId: 'tor' }).onChange((status) => {
      torRunning = status?.desired.main === 'running'
      return { cancel: false }
    })
  }

  // Knuth: -c config, --init_run, --network <name> (v1.3.0; old --chipnet flags are no-ops)
  const knuthArgs: string[] = [
    '-c',
    `${rootDir}/kth.cfg`,
    '--init_run',
    '--network',
    netName,
  ]

  const knuthSub = await sdk.SubContainer.of(
    effects,
    { imageId: 'knuth' },
    mainMounts,
    'knuth-sub',
  )

  // Helper: JSON-RPC via curl (no knuth-cli). Retry transient attach failures
  // the way BCHN retries bitcoin-cli under mount-namespace pressure.
  async function rpcCall(method: string) {
    const args = [
      'curl',
      '-s',
      '--max-time',
      '10',
      '--user',
      `${rpcUser}:${rpcPassword}`,
      '-H',
      'content-type: application/json',
      '-d',
      JSON.stringify({ jsonrpc: '2.0', id: 1, method, params: [] }),
      `http://127.0.0.1:${rpcPort}/`,
    ]
    let lastErr: unknown
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        return await knuthSub.exec(args)
      } catch (err) {
        lastErr = err
        await new Promise((r) => setTimeout(r, 500))
      }
    }
    throw lastErr
  }

  // debug.log is huge and chatty. Pull only the sync/peer lines from the
  // last 2 MiB — a raw `tail -n 400` is all handshake noise and misses
  // "Fully synced" / "Peers:".
  async function debugLogSnippet(): Promise<string> {
    try {
      const res = await knuthSub.exec([
        'sh',
        '-c',
        "tail -c 2000000 /data/debug.log 2>/dev/null | grep -E 'Fully synced at height|Stats: .* blocks at height|Peers:|Validation complete:|header_height=|block_height=' | tail -n 80 || true",
      ])
      return String(res.stdout ?? '')
    } catch {
      return ''
    }
  }

  function parsePeerCount(log: string): number | null {
    const matches = [...log.matchAll(/Peers:\s+(\d+)\s*\/\s*(\d+)/g)]
    const last = matches.at(-1)
    return last ? Number(last[1]) : null
  }

  function parseLogHeights(
    log: string,
  ): { blocks: number; headers: number } | null {
    // Knuth's own coordinator is the source of truth. RPC getblockchaininfo
    // often reports blocks=0 / genesis even after "Fully synced at height N".
    const fully = [...log.matchAll(/Fully synced at height (\d+)/g)]
    if (fully.length) {
      const n = Number(fully.at(-1)![1])
      return { blocks: n, headers: n }
    }
    const stats = [...log.matchAll(/Stats:\s+(\d+)\s+blocks at height (\d+)/g)]
    if (stats.length) {
      const m = stats.at(-1)!
      return { blocks: Number(m[1]), headers: Number(m[2]) }
    }
    const totals = [...log.matchAll(/Validation complete:.*?total size (\d+)/g)]
    const headerMs = [...log.matchAll(/header_height=(\d+)/g)]
    const blockMs = [...log.matchAll(/block_height=(\d+)/g)]
    const headers = totals.length
      ? Number(totals.at(-1)![1])
      : headerMs.length
        ? Number(headerMs.at(-1)![1])
        : 0
    const blocks = blockMs.length ? Number(blockMs.at(-1)![1]) : 0
    if (headers === 0 && blocks === 0) return null
    return { headers, blocks }
  }

  async function nodeIsUp(): Promise<boolean> {
    try {
      const res = await knuthSub.exec(['sh', '-c', 'pgrep -x kth >/dev/null'])
      return res.exitCode === 0
    } catch {
      return false
    }
  }

  return (
    sdk.Daemons.of(effects)
      .addDaemon('primary', {
        subcontainer: knuthSub,
        exec: {
          command: ['kth', ...knuthArgs],
          // 5 min left the UI "Stopped" while kth kept running, so Delete
          // Peer List / Delete Test Network Data never actually took effect.
          sigtermTimeout: 45_000,
        },
        ready: {
          // Same row label as BCHN/BCHD/Flowee. Do not stall the whole health
          // page if RPC is off or still coming up — other checks require this.
          display: 'RPC',
          fn: async () => {
            if (rpcEnabled) {
              try {
                const res = await rpcCall('getblockchaininfo')
                if (res.exitCode === 0 && res.stdout) {
                  const body = JSON.parse(String(res.stdout))
                  if (body?.result) {
                    return {
                      message: `The Knuth RPC interface is ready (${netLabel})`,
                      result: 'success' as const,
                    }
                  }
                }
              } catch {
                /* fall through */
              }
              if (await nodeIsUp()) {
                return {
                  message: 'The Knuth RPC interface is not ready',
                  result: 'starting' as const,
                }
              }
              return {
                message: 'The Knuth RPC interface is not ready',
                result: 'starting' as const,
              }
            }

            if (await nodeIsUp()) {
              return {
                message: `JSON-RPC is off — node is running (${netLabel})`,
                result: 'success' as const,
              }
            }
            return {
              message: 'Knuth is starting...',
              result: 'starting' as const,
            }
          },
        },
        requires: [],
      })
      .addHealthCheck('sync-progress', {
        ready: {
          display: 'Blockchain Sync',
          fn: async () => {
            const logHeights = parseLogHeights(await debugLogSnippet())

            if (rpcEnabled) {
              try {
                const res = await rpcCall('getblockchaininfo')
                if (res.exitCode === 0 && res.stdout) {
                  const info = JSON.parse(String(res.stdout))?.result
                  if (info) {
                    let blocks = Number(info.blocks ?? 0)
                    let headers = Number(info.headers ?? 0)
                    // Prefer the coordinator log when RPC is stuck at genesis.
                    if (logHeights) {
                      if (logHeights.blocks > blocks) blocks = logHeights.blocks
                      if (logHeights.headers > headers)
                        headers = logHeights.headers
                    }
                    if (headers === 0) {
                      return {
                        message: `Connecting to ${netLabel} peers and fetching headers...`,
                        result: 'loading' as const,
                      }
                    }
                    if (blocks < headers) {
                      const pct = ((blocks / headers) * 100).toFixed(2)
                      return {
                        message: `Syncing blocks... ${pct}% (${netLabel})`,
                        result: 'loading' as const,
                      }
                    }
                    return {
                      message: `Synced — block ${blocks} (${netLabel})`,
                      result: 'success' as const,
                    }
                  }
                }
              } catch {
                /* fall through to logs */
              }
            }

            const heights = logHeights
            if (!heights || (heights.headers === 0 && heights.blocks === 0)) {
              return {
                message: `Connecting to ${netLabel} peers and fetching headers...`,
                result: 'loading' as const,
              }
            }
            if (heights.blocks < heights.headers) {
              const pct = (
                (heights.blocks / Math.max(heights.headers, 1)) *
                100
              ).toFixed(2)
              return {
                message: `Syncing blocks... ${pct}% (${netLabel})`,
                result: 'loading' as const,
              }
            }
            return {
              message: `Synced — block ${heights.blocks} (${netLabel})`,
              result: 'success' as const,
            }
          },
        },
        requires: ['primary'],
      })
      .addOneshot('synced-true', {
        subcontainer: null,
        exec: {
          fn: async () => {
            const currentStore = await storeJson.read().once()
            if (!currentStore?.fullySynced) {
              await storeJson.merge(effects, { fullySynced: true })
            }
            return null
          },
        },
        requires: ['sync-progress'],
      })
      .addHealthCheck('peer-connections', {
        ready: {
          display: 'Peer Connections',
          fn: async () => {
            const count = parsePeerCount(await debugLogSnippet())
            if (count === null) {
              return {
                message:
                  'No peers connected — node may be starting up or isolated',
                result: 'loading' as const,
              }
            }
            if (count === 0) {
              return {
                message:
                  'No peers connected — node may be starting up or isolated',
                result: 'loading' as const,
              }
            }
            if (count < 3) {
              return {
                message: `Only ${count} peer(s) connected — network connectivity may be limited`,
                result: 'loading' as const,
              }
            }
            return {
              message: `${count} peers`,
              result: 'success' as const,
            }
          },
        },
        requires: ['primary'],
      })
      .addHealthCheck('tor', {
        ready: {
          display: 'Tor',
          fn: () => {
            if (!torEnabled) {
              return {
                result: 'disabled' as const,
                message: 'Tor is optional and currently off',
              }
            }
            if (!torIp) {
              return {
                result: 'disabled' as const,
                message: 'Tor is not installed',
              }
            }
            if (!torRunning) {
              return {
                result: 'disabled' as const,
                message: 'Tor is not running',
              }
            }
            return {
              result: 'success' as const,
              message: 'Outbound only. Add an onion address to enable inbound.',
            }
          },
        },
        requires: [],
      })
      .addHealthCheck('i2p', {
        ready: {
          display: 'I2P',
          fn: () => ({
            result: 'disabled' as const,
            message: 'I2P support is not implemented yet.',
          }),
        },
        requires: [],
      })
      .addHealthCheck('clearnet', {
        ready: {
          display: 'Clearnet',
          fn: () => ({
            result: 'success' as const,
            message: 'Outbound only. Publish an IP address to enable inbound.',
          }),
        },
        requires: [],
      })
      // Knuth-specific capability rows. Neither is a network service — UTXO-Z is a
      // storage engine and the C-API is an in-process binding — so they surface
      // here rather than as interfaces (kth listens on exactly two ports: P2P and
      // JSON-RPC, verified against the running node).
      .addHealthCheck('utxoz', {
        ready: {
          display: 'UTXO-Z Storage',
          fn: async () => {
            if (!utxozEnabled)
              return {
                result: 'disabled' as const,
                message: 'UTXO-Z capability is disabled in Node Settings',
              }
            try {
              const res = await knuthSub.exec([
                'test',
                '-d',
                `${dataDir}/utxoz`,
              ])
              if (res.exitCode === 0)
                return {
                  result: 'success' as const,
                  message: `UTXO-Z database active at ${dataDir}/utxoz`,
                }
              return {
                result: 'loading' as const,
                message: 'Waiting for the UTXO-Z database to be created...',
              }
            } catch {
              return {
                result: 'loading' as const,
                message: 'Waiting for the UTXO-Z database to be created...',
              }
            }
          },
        },
        requires: ['primary'],
      })
      .addHealthCheck('ipc-capi', {
        ready: {
          display: 'IPC / C-API',
          fn: () =>
            ipcEnabled
              ? {
                  result: 'success' as const,
                  message:
                    'C-API capability advertised for dependent services (in-process binding, not a network port)',
                }
              : {
                  result: 'disabled' as const,
                  message:
                    'IPC / C-API capability is disabled in Node Settings',
                },
        },
        requires: [],
      })
  )
})
