import { sdk } from './sdk'
import { peerInterfaceId, rpcInterfaceId, networkPorts, Network } from './utils'
import { storeJson } from './fileModels/store.json'
import { i18n } from './i18n'

export const setInterfaces = sdk.setupInterfaces(async ({ effects }) => {
  const store = await storeJson.read().once()
  const network: Network = store?.network ?? 'mainnet'
  const { peer: peerPort, rpc: rpcPort } = networkPorts[network]
  const rpcEnabled = store?.rpcEnabled ?? false

  const receipts = []

  // ── P2P ──────────────────────────────────────────────────────────────
  const peerMulti = sdk.MultiHost.of(effects, 'peer')
  const peerOrigin = await peerMulti.bindPort(peerPort, {
    protocol: null,
    preferredExternalPort: peerPort,
    addSsl: null,
    secure: { ssl: false },
  })
  const peer = sdk.createInterface(effects, {
    name: i18n('Peer Interface'),
    id: peerInterfaceId,
    description: i18n('Peer-to-peer connections on the Bitcoin Cash network'),
    type: 'p2p',
    masked: false,
    schemeOverride: { ssl: null, noSsl: null },
    username: null,
    path: '',
    query: {},
  })
  receipts.push(await peerOrigin.export([peer]))

  // ── JSON-RPC (kth v1.3.0+) ───────────────────────────────────────────────
  if (rpcEnabled) {
    const rpcMulti = sdk.MultiHost.of(effects, 'rpc')
    const rpcOrigin = await rpcMulti.bindPort(rpcPort, {
      protocol: null,
      preferredExternalPort: rpcPort,
      addSsl: null,
      secure: { ssl: false },
    })
    const rpc = sdk.createInterface(effects, {
      name: i18n('JSON-RPC Interface'),
      id: rpcInterfaceId,
      description:
        i18n('Bitcoin-Cash-compatible JSON-RPC for mining pools and other services'),
      type: 'api',
      masked: true,
      schemeOverride: { ssl: null, noSsl: null },
      username: store?.rpcUser ?? null,
      path: '',
      query: {},
    })
    receipts.push(await rpcOrigin.export([rpc]))
  }

  return receipts
})
