import { sdk } from '../sdk'
import { knuthConf } from '../file-models/knuth.conf'
import { storeJson } from '../file-models/store.json'
import { networkPorts, Network } from '../utils'

const randomHex = (bytes: number) =>
  Array.from({ length: bytes }, () =>
    Math.floor(Math.random() * 256)
      .toString(16)
      .padStart(2, '0'),
  ).join('')

export const seedFiles = sdk.setupOnInit(async (effects) => {
  // Seed default store on first install
  const existing = await storeJson.read().once()
  if (!existing) {
    await storeJson.merge(effects, {
      ipcEnabled: true,
      utxozEnabled: true,
      torEnabled: false,
      rpcEnabled: false,
    })
  }

  // Generate RPC credentials once, then keep them stable across restarts.
  const store = await storeJson.read().once()
  if (!store?.rpcUser || !store?.rpcPassword) {
    await storeJson.merge(effects, {
      rpcUser: 'knuth',
      rpcPassword: randomHex(16),
    })
  }

  // Seed default config: credentials plus the ports for the active network.
  // rpc.bind must be written explicitly — kth defaults it to 127.0.0.1, which
  // would make the RPC unreachable from other containers.
  const seeded = await storeJson.read().once()
  const network: Network = seeded?.network ?? 'mainnet'
  const { peer: peerPort, rpc: rpcPort } = networkPorts[network]
  await knuthConf.merge(effects, {
    'net.inbound_port': peerPort,
    'rpc.bind': '0.0.0.0',
    'rpc.port': rpcPort,
    'rpc.user': seeded?.rpcUser ?? '',
    'rpc.password': seeded?.rpcPassword ?? '',
  })
})
