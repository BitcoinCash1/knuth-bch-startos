import { sdk } from './sdk'
import { rootDir, rpcPort } from './utils'
import { knuthConf } from './file-models/knuth.conf'
import { storeJson } from './file-models/store.json'

export const main = sdk.setupMain(async ({ effects }) => {
  console.log('Starting Knuth!')

  const conf = await knuthConf.read().const(effects)
  const store = await storeJson.read().once()
  const rpcUser = store?.rpcUser ?? 'knuth-bch'
  const rpcPassword = store?.rpcPassword ?? ''
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

  const knuthArgs: string[] = [
    `--conf=${rootDir}/kth-node.cfg`,
    `--datadir=${rootDir}`,
    `--txindex`,
    `--server`,
    `--rpcuser=${rpcUser}`,
    `--rpcpassword=${rpcPassword}`,
    `--rpcallowip=0.0.0.0/0`,
    `--rpcbind=0.0.0.0`,
    `--rpcport=${rpcPort}`,
    `--listen=1`,
  ]

  // ZMQ endpoints
  if (conf?.zmqpubhashblock) {
    knuthArgs.push(`--zmqpubhashblock=${conf.zmqpubhashblock}`)
    knuthArgs.push(`--zmqpubrawtx=${conf.zmqpubrawtx}`)
    knuthArgs.push(`--zmqpubhashtx=${conf.zmqpubhashtx}`)
    knuthArgs.push(`--zmqpubrawblock=${conf.zmqpubrawblock}`)
  }

  // Tor proxy
  if (torIp) {
    knuthArgs.push(`--proxy=${torIp}:9050`)
    knuthArgs.push(`--onion=${torIp}:9050`)
  }

  if (conf?.dbcache) {
    knuthArgs.push(`--dbcache=${conf.dbcache}`)
  }
  if (conf?.maxconnections) {
    knuthArgs.push(`--maxconnections=${conf.maxconnections}`)
  }

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

  async function rpc(method: string, ...params: string[]) {
    const body = JSON.stringify({
      jsonrpc: '1.0',
      id: 'healthcheck',
      method,
      params,
    })
    const result = await knuthSub.exec([
      'curl',
      '-sf',
      '--data-binary',
      body,
      '-H',
      'content-type: text/plain;',
      `http://${rpcUser}:${rpcPassword}@127.0.0.1:${rpcPort}/`,
    ])
    if (result.exitCode !== 0) throw new Error('RPC call failed')
    return JSON.parse(result.stdout.toString())
  }

  return sdk.Daemons.of(effects)
    .addDaemon('primary', {
      subcontainer: knuthSub,
      exec: {
        command: ['kth-node', ...knuthArgs],
        sigtermTimeout: 300_000,
      },
      ready: {
        display: 'RPC',
        fn: async () => {
          try {
            const res = await rpc('getblockchaininfo')
            return res?.result
              ? { message: 'Knuth RPC is ready', result: 'success' }
              : { message: 'Knuth RPC is starting...', result: 'starting' }
          } catch {
            return { message: 'Knuth RPC is starting...', result: 'starting' }
          }
        },
      },
      requires: [],
    })
    .addHealthCheck('sync-progress', {
      ready: {
        display: 'Blockchain Sync',
        fn: async () => {
          try {
            const res = await rpc('getblockchaininfo')
            const info = res?.result as {
              blocks: number
              headers: number
              verificationprogress: number
              initialblockdownload: boolean
            }
            if (!info)
              return { message: 'Waiting for sync info', result: 'loading' }
            if (info.initialblockdownload) {
              const pct = (info.verificationprogress * 100).toFixed(2)
              return {
                message: `Syncing blocks... ${pct}%`,
                result: 'loading',
              }
            }
            return {
              message: `Synced — block ${info.blocks}`,
              result: 'success',
            }
          } catch {
            return { message: 'Waiting for sync info', result: 'loading' }
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
          try {
            const res = await rpc('getpeerinfo')
            const peers = (res?.result ?? []) as Array<{ inbound: boolean }>
            const count = peers.length
            if (count === 0)
              return {
                message: 'No peers connected — node may be starting up',
                result: 'loading',
              }
            if (count < 3)
              return {
                message: `Only ${count} peer(s) connected`,
                result: 'loading',
              }
            const inbound = peers.filter((p) => p.inbound).length
            return {
              message: `${count} peers (${count - inbound} outbound, ${inbound} inbound)`,
              result: 'success',
            }
          } catch {
            return { message: 'Unable to query peers', result: 'loading' }
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
