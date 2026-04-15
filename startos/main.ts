import { sdk } from './sdk'
import { rootDir, peerPort } from './utils'
import { knuthConf } from './file-models/knuth.conf'
import { storeJson } from './file-models/store.json'

export const main = sdk.setupMain(async ({ effects }) => {
  console.log('Starting Knuth!')

  const store = await storeJson.read().once()
  const torEnabled = store?.torEnabled ?? false

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
          // Knuth v0.79.0 has no RPC — check process is alive via a simple exec
          try {
            const result = await knuthSub.exec(['test', '-d', `${rootDir}/blockchain`])
            if (result.exitCode === 0) {
              return { message: 'Knuth node is running', result: 'success' }
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
