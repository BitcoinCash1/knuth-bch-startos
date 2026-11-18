import { sdk } from './sdk'
import { peerInterfaceId, rpcInterfaceId, networkPorts, Network } from './utils'
import { storeJson } from './fileModels/store.json'
import { i18n } from './i18n'

export const setInterfaces = sdk.setupInterfaces(async ({ effects }) => {
  const store = await storeJson
    .read((s) => ({
      network: s.network,
      rpcEnabled: s.rpcEnabled,
      rpcUser: s.rpcUser,
    }))
    .const(effects)
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
      description: i18n(
        'Bitcoin-Cash-compatible JSON-RPC for mining pools and other services',
      ),
      type: 'api',
      masked: true,
      schemeOverride: { ssl: null, noSsl: null },
      username: store?.rpcUser ?? null,
      path: '',
      query: {},
    })
    receipts.push(await rpcOrigin.export([rpc]))
  }

  // TEST ONLY, Job Declaration client (k-nuth/kth#832, #834) on chipnet
  // against the BCH P2Pool pool. Same raw TCP shape as the peer interface.
  if (network === 'chipnet') {
    const sv2Port = 3335
    const sv2Multi = sdk.MultiHost.of(effects, 'sv2')
    const sv2Origin = await sv2Multi.bindPort(sv2Port, {
      protocol: null,
      preferredExternalPort: sv2Port,
      addSsl: null,
      secure: { ssl: false },
    })
    const sv2 = sdk.createInterface(effects, {
      name: i18n('Stratum V2 (test)'),
      id: 'sv2',
      description: i18n(
        'Stratum V2 listener for the chipnet Job Declaration test',
      ),
      type: 'p2p',
      masked: false,
      schemeOverride: { ssl: null, noSsl: null },
      username: null,
      path: '',
      query: {},
    })
    receipts.push(await sv2Origin.export([sv2]))
  }

  return receipts
})
