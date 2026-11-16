import { sdk } from './sdk'
import {
  rootDir,
  networkPorts,
  networkName,
  networkDbDir,
  networkHostsFile,
  Network,
} from './utils'
import { storeJson } from './fileModels/store.json'
import { knuthConf } from './fileModels/knuth.conf'
import { mainMounts } from './mounts'
import { i18n } from './i18n'

export { mainMounts }

export const main = sdk.setupMain(async ({ effects }) => {
  console.log(i18n('Starting Knuth!'))

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

  // kth tells chipnet from testnet4 (same network magic) only by
  // net.inbound_port == 48333, and kth.cfg is read after --network, so a port
  // left over from another network silently runs testnet4 parameters on
  // chipnet. Reassert the active network's port, chain directory and hosts
  // file on every start.
  await knuthConf.merge(effects, {
    'net.inbound_port': networkPorts[network].peer,
    'net.hosts_file': networkHostsFile(network),
    'db.directory': dataDir,
  })

  // kth serves the public per-network RPC port itself. It must bind every
  // interface so dependents can reach it from their own containers.
  if (rpcEnabled) {
    await knuthConf.merge(effects, {
      'rpc.bind': '0.0.0.0',
      'rpc.port': rpcPort,
      'rpc.enabled': true,
    })
  }

  const knuthSub = await sdk.SubContainer.of(
    effects,
    { imageId: 'knuth' },
    mainMounts,
    'knuth-sub',
  )

  // Helper: JSON-RPC via curl (no knuth-cli). Retry transient attach failures
  // the way BCHN retries bitcoin-cli under mount-namespace pressure.
  async function rpcCall(method: string, params: unknown[] = []) {
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
      JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
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

  // Read a bounded tail because the debug log can be several gigabytes.
  async function debugLogSnippet(): Promise<string> {
    try {
      const res = await knuthSub.exec([
        'sh',
        '-c',
        "tail -c 2000000 /data/debug.log 2>/dev/null | grep 'Peers:' | tail -n 10 || true",
      ])
      return String(res.stdout ?? '')
    } catch {
      return ''
    }
  }

  function parsePeerCount(log: string): number | null {
    const lines = log.trim().split('\n')
    for (const line of lines.reverse()) {
      const match = line.match(/Peers:\s+(\d+)\s*\/\s*\d+/)
      if (!match) continue
      const timestamp = line.match(
        /^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\]/,
      )
      if (!timestamp) return null
      const age = Date.now() - Date.parse(timestamp[1].replace(' ', 'T') + 'Z')
      return Number.isFinite(age) && age >= -60_000 && age < 120_000
        ? Number(match[1])
        : null
    }
    return null
  }

  // A node cut off from the chain (no peers, every peer banned, or parked on a
  // stale branch it cannot leave) still finds its tip at the top of its own
  // headers, so the height comparison alone reads "Synced". Before reporting
  // Synced, require a peer and a tip that is not hours old. Regtest only moves
  // when blocks are generated and scalenet idles for long stretches.
  const staleTipSeconds =
    network === 'regtest' ? null : network === 'scalenet' ? 24 * 3600 : 3 * 3600

  function formatAge(seconds: number): string {
    const hours = Math.floor(seconds / 3600)
    return hours >= 48 ? `${Math.floor(hours / 24)} d` : `${hours} h`
  }

  async function tipAgeSeconds(hash: unknown): Promise<number | null> {
    if (typeof hash !== 'string' || !hash) return null
    try {
      const res = await rpcCall('getblockheader', [hash, true])
      const time = Number(JSON.parse(String(res.stdout ?? ''))?.result?.time)
      if (!Number.isFinite(time) || time <= 0) return null
      return Math.floor(Date.now() / 1000) - time
    } catch {
      return null
    }
  }

  async function syncedUnlessCutOff(
    blocks: number,
    peers: number | null,
    tipHash?: unknown,
  ) {
    if (network !== 'regtest' && peers === null) {
      return {
        message: i18n('Waiting for current peer status (${netLabel})', {
          netLabel,
        }),
        result: 'loading' as const,
      }
    }
    if (network !== 'regtest' && peers === 0) {
      return {
        message: i18n(
          'No peers connected — block ${blocks} may not be the tip (${netLabel})',
          { blocks: String(blocks), netLabel },
        ),
        result: 'loading' as const,
      }
    }
    if (staleTipSeconds !== null) {
      const age = await tipAgeSeconds(tipHash)
      if (age === null) {
        return {
          message: i18n(
            'Waiting for a readable validated block tip (${netLabel})',
            { netLabel },
          ),
          result: 'loading' as const,
        }
      }
      if (age !== null && age > staleTipSeconds) {
        return {
          message: i18n(
            'Behind the network — newest block ${blocks} is ${age} old (${netLabel})',
            { blocks: String(blocks), age: formatAge(age), netLabel },
          ),
          result: 'loading' as const,
        }
      }
    }
    return {
      message: i18n('Synced — block ${blocks} (${netLabel})', {
        blocks: String(blocks),
        netLabel,
      }),
      result: 'success' as const,
    }
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
          display: i18n('RPC'),
          fn: async () => {
            if (rpcEnabled) {
              try {
                const res = await rpcCall('getblockchaininfo')
                if (res.exitCode === 0 && res.stdout) {
                  const body = JSON.parse(String(res.stdout))
                  if (body?.result) {
                    return {
                      message: i18n(
                        'The Knuth RPC interface is ready (${netLabel})',
                        { netLabel },
                      ),
                      result: 'success' as const,
                    }
                  }
                }
              } catch {
                /* fall through */
              }
              if (await nodeIsUp()) {
                return {
                  message: i18n('The Knuth RPC interface is not ready'),
                  result: 'starting' as const,
                }
              }
              return {
                message: i18n('The Knuth RPC interface is not ready'),
                result: 'starting' as const,
              }
            }

            if (await nodeIsUp()) {
              return {
                message: i18n(
                  'JSON-RPC is off — node is running (${netLabel})',
                  { netLabel },
                ),
                result: 'success' as const,
              }
            }
            return {
              message: i18n('Knuth is starting...'),
              result: 'starting' as const,
            }
          },
        },
        requires: [],
      })
      .addHealthCheck('sync-progress', {
        ready: {
          display: i18n('Blockchain Sync'),
          fn: async () => {
            const log = await debugLogSnippet()
            const peers = parsePeerCount(log)

            if (rpcEnabled) {
              try {
                // In 1.4, blocks/hash describe the validated chain. Raw block
                // files and header heights must never advance that result.
                const res = await rpcCall('getblockchaininfo')
                if (res.exitCode === 0 && res.stdout) {
                  const info = JSON.parse(String(res.stdout))?.result
                  if (info) {
                    const blocks = Number(info.blocks ?? 0)
                    const headers = Number(info.headers ?? 0)
                    if (headers === 0) {
                      return {
                        message: i18n(
                          'Connecting to ${netLabel} peers and fetching headers...',
                          { netLabel },
                        ),
                        result: 'loading' as const,
                      }
                    }
                    if (blocks < headers) {
                      const pct = ((blocks / headers) * 100).toFixed(2)
                      return {
                        message: i18n(
                          'Syncing blocks... ${pct}% (${netLabel})',
                          { pct, netLabel },
                        ),
                        result: 'loading' as const,
                      }
                    }
                    return syncedUnlessCutOff(blocks, peers, info.bestblockhash)
                  }
                }
              } catch {
                /* report that verification is unavailable */
              }
            }

            return {
              message: i18n(
                'Unable to verify synchronization — enable JSON-RPC (${netLabel})',
                { netLabel },
              ),
              result: 'loading' as const,
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
          display: i18n('Peer Connections'),
          fn: async () => {
            const count = parsePeerCount(await debugLogSnippet())
            if (count === null) {
              return {
                message: i18n(
                  'No peers connected — node may be starting up or isolated',
                ),
                result: 'loading' as const,
              }
            }
            if (count === 0) {
              return {
                message: i18n(
                  'No peers connected — node may be starting up or isolated',
                ),
                result: 'loading' as const,
              }
            }
            if (count < 3) {
              return {
                message: i18n(
                  'Only ${count} peer(s) connected — network connectivity may be limited',
                  { count: String(count) },
                ),
                result: 'loading' as const,
              }
            }
            return {
              message: i18n('${count} peers', { count: String(count) }),
              result: 'success' as const,
            }
          },
        },
        requires: ['primary'],
      })
      .addHealthCheck('tor', {
        ready: {
          display: i18n('Tor'),
          fn: () => {
            if (!torEnabled) {
              return {
                result: 'disabled' as const,
                message: i18n('Tor is optional and currently off'),
              }
            }
            if (!torIp) {
              return {
                result: 'disabled' as const,
                message: i18n('Tor is not installed'),
              }
            }
            if (!torRunning) {
              return {
                result: 'disabled' as const,
                message: i18n('Tor is not running'),
              }
            }
            return {
              result: 'success' as const,
              message: i18n(
                'Outbound only. Add an onion address to enable inbound.',
              ),
            }
          },
        },
        requires: [],
      })
      .addHealthCheck('i2p', {
        ready: {
          display: i18n('I2P'),
          fn: () => ({
            result: 'disabled' as const,
            message: i18n('I2P support is not implemented yet.'),
          }),
        },
        requires: [],
      })
      .addHealthCheck('clearnet', {
        ready: {
          display: i18n('Clearnet'),
          fn: () => ({
            result: 'success' as const,
            message: i18n(
              'Outbound only. Publish an IP address to enable inbound.',
            ),
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
          display: i18n('UTXO-Z Storage'),
          fn: async () => {
            if (!utxozEnabled)
              return {
                result: 'disabled' as const,
                message: i18n('UTXO-Z capability is disabled in Node Settings'),
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
                  message: i18n('UTXO-Z database active at ${dataDir}/utxoz', {
                    dataDir,
                  }),
                }
              return {
                result: 'loading' as const,
                message: i18n(
                  'Waiting for the UTXO-Z database to be created...',
                ),
              }
            } catch {
              return {
                result: 'loading' as const,
                message: i18n(
                  'Waiting for the UTXO-Z database to be created...',
                ),
              }
            }
          },
        },
        requires: ['primary'],
      })
      .addHealthCheck('ipc-capi', {
        ready: {
          display: i18n('IPC / C-API'),
          fn: () =>
            ipcEnabled
              ? {
                  result: 'success' as const,
                  message: i18n(
                    'C-API capability advertised for dependent services (in-process binding, not a network port)',
                  ),
                }
              : {
                  result: 'disabled' as const,
                  message: i18n(
                    'IPC / C-API capability is disabled in Node Settings',
                  ),
                },
        },
        requires: [],
      })
  )
})
