import { sdk } from './sdk'
import { rootDir, networkPorts, networkFlag, Network } from './utils'
import { knuthConf } from './file-models/knuth.conf'
import { storeJson } from './file-models/store.json'

export const main = sdk.setupMain(async ({ effects }) => {
  console.log('Starting Knuth!')

  const store = await storeJson.read().once()
  const network: Network = store?.network ?? 'mainnet'
  const { peer: peerPort, rpc: rpcPort } = networkPorts[network]
  const netFlag = networkFlag[network]
  const netLabel = network.charAt(0).toUpperCase() + network.slice(1)
  const torEnabled = store?.torEnabled ?? false
  const rpcEnabled = store?.rpcEnabled ?? false
  const rpcUser = store?.rpcUser ?? ''
  const rpcPassword = store?.rpcPassword ?? ''

  // Tor — get container IP
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

  // Knuth uses -c <config> and --init_run (init chain if needed, then run)
  const knuthArgs: string[] = [
    '-c', `${rootDir}/kth.cfg`,
    '--init_run',
    ...(netFlag ? [netFlag] : []),
  ]

  const mounts = sdk.Mounts.of().mountVolume({
    volumeId: 'main',
    subpath: null,
    mountpoint: rootDir,
    readonly: false,
  })

  const knuthSub = await sdk.SubContainer.of(
    effects,
    { imageId: 'knuth' },
    mounts,
    'knuth-sub',
  )

  return sdk.Daemons.of(effects)
    .addDaemon('primary', {
      subcontainer: knuthSub,
      exec: {
        command: ['kth', ...knuthArgs],
        sigtermTimeout: 300_000,
      },
      ready: {
        display: 'Node',
        fn: async () => {
          // With RPC enabled (kth v1.3.0+) ask the node where it is. Knuth's
          // getblockchaininfo returns chain/blocks/headers/best_block_hash/
          // difficulty — there is no initialblockdownload or
          // verificationprogress field to lean on, so blocks vs headers is the
          // only sync signal available here.
          if (rpcEnabled) {
            try {
              const res = await knuthSub.exec([
                'curl', '-s', '--max-time', '10',
                '--user', `${rpcUser}:${rpcPassword}`,
                '-H', 'content-type: application/json',
                '-d', '{"jsonrpc":"2.0","id":1,"method":"getblockchaininfo","params":[]}',
                `http://127.0.0.1:${rpcPort}/`,
              ])
              if (res.exitCode === 0 && res.stdout) {
                const body = JSON.parse(String(res.stdout))
                const info = body?.result
                if (info) {
                  const blocks = Number(info.blocks ?? 0)
                  const headers = Number(info.headers ?? 0)
                  if (headers > 0 && blocks < headers) {
                    const pct = ((blocks / headers) * 100).toFixed(2)
                    return {
                      message: `Syncing ${netLabel}: ${blocks} / ${headers} (${pct}%)`,
                      result: 'starting' as const,
                    }
                  }
                  return {
                    message: `Knuth is synced at height ${blocks} (${netLabel})`,
                    result: 'success' as const,
                  }
                }
              }
            } catch {
              // fall through to the liveness check below
            }
          }

          // No RPC (disabled, or a binary built without the `rpc` conan
          // option) — fall back to a liveness check.
          try {
            const result = await knuthSub.exec(['test', '-d', `${rootDir}/blockchain`])
            if (result.exitCode === 0) {
              return { message: `Knuth node is running (${netLabel})`, result: 'success' }
            }
            return { message: 'Knuth is initializing...', result: 'starting' }
          } catch {
            return { message: 'Knuth is starting...', result: 'starting' }
          }
        },
      },
      requires: [],
    })
    .addHealthCheck('tor', {
      ready: {
        display: 'Tor',
        fn: () => {
          if (!torEnabled)
            return { result: 'disabled' as const, message: 'Tor routing is disabled in config' }
          if (!torIp)
            return { result: 'disabled' as const, message: 'Tor is not installed' }
          if (!torRunning)
            return { result: 'disabled' as const, message: 'Tor is not running' }
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
              message: 'Outbound via Tor proxy — clearnet peers still reachable',
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
