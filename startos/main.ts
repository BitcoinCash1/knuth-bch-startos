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

  return sdk.Daemons.of(effects)
    .addDaemon('primary', {
      subcontainer: knuthSub,
      exec: {
        command: ['kth', ...knuthArgs],
        sigtermTimeout: 300_000,
      },
      ready: {
        // Mirror BCHN: when RPC is on, "ready" means the RPC interface answers.
        // When RPC is off, fall back to chaindir liveness.
        display: rpcEnabled ? 'RPC' : 'Node',
        fn: async () => {
          if (rpcEnabled) {
            try {
              const res = await rpcCall('getblockchaininfo')
              if (res.exitCode === 0 && res.stdout) {
                const body = JSON.parse(String(res.stdout))
                if (body?.result) {
                  return {
                    message: `Knuth RPC is ready (${netLabel})`,
                    result: 'success' as const,
                  }
                }
              }
              return {
                message: 'The Knuth RPC interface is not ready',
                result: 'starting' as const,
              }
            } catch {
              return {
                message: 'The Knuth RPC interface is not ready',
                result: 'starting' as const,
              }
            }
          }

          try {
            const result = await knuthSub.exec(['test', '-d', dataDir])
            if (result.exitCode === 0) {
              return {
                message: `Knuth node is running (${netLabel})`,
                result: 'success' as const,
              }
            }
            return {
              message: 'Knuth is initializing...',
              result: 'starting' as const,
            }
          } catch {
            return {
              message: 'Knuth is starting...',
              result: 'starting' as const,
            }
          }
        },
      },
      requires: [],
    })
    .addHealthCheck('sync-progress', {
      ready: {
        display: 'Blockchain Sync',
        fn: async () => {
          if (!rpcEnabled) {
            return {
              result: 'disabled' as const,
              message:
                'Enable JSON-RPC in Node Settings to show sync progress',
            }
          }
          try {
            const res = await rpcCall('getblockchaininfo')
            if (res.exitCode !== 0 || !res.stdout) {
              return {
                message: 'Waiting for sync info',
                result: 'loading' as const,
              }
            }
            const body = JSON.parse(String(res.stdout))
            const info = body?.result
            if (!info) {
              return {
                message: 'Waiting for sync info',
                result: 'loading' as const,
              }
            }
            // Knuth has no initialblockdownload / verificationprogress — use
            // blocks vs headers (same signal as Fulcrum-style health elsewhere).
            const blocks = Number(info.blocks ?? 0)
            const headers = Number(info.headers ?? 0)
            if (headers === 0) {
              return {
                message: `Connecting to ${netLabel} peers and fetching headers...`,
                result: 'loading' as const,
              }
            }
            if (blocks < headers) {
              const pct = ((blocks / headers) * 100).toFixed(2)
              return {
                message: `Syncing ${netLabel}: ${blocks} / ${headers} (${pct}%)`,
                result: 'loading' as const,
              }
            }
            return {
              message: `Synced — block ${blocks} (${netLabel})`,
              result: 'success' as const,
            }
          } catch {
            return {
              message: 'Waiting for sync info',
              result: 'loading' as const,
            }
          }
        },
      },
      requires: ['primary'],
    })
    .addHealthCheck('tor', {
      ready: {
        display: 'Tor',
        fn: () => {
          if (!torEnabled)
            return {
              result: 'disabled' as const,
              message: 'Tor routing is disabled in config',
            }
          if (!torIp)
            return {
              result: 'disabled' as const,
              message: 'Tor is not installed',
            }
          if (!torRunning)
            return {
              result: 'disabled' as const,
              message: 'Tor is not running',
            }
          return {
            result: 'success' as const,
            message: 'All connections routed through Tor',
          }
        },
      },
      requires: [],
    })
    .addHealthCheck('clearnet', {
      ready: {
        display: 'Clearnet',
        fn: () => {
          if (torEnabled && torIp)
            return {
              result: 'success' as const,
              message:
                'Outbound via Tor proxy — clearnet peers still reachable',
            }
          return {
            result: 'success' as const,
            message: 'Direct clearnet connections',
          }
        },
      },
      requires: [],
    })
})
