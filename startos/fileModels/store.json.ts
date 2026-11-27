import { FileHelper, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'

export const shape = z.object({
  // Enum order matches BCHN store / Network action dropdown.
  network: z
    .enum(['mainnet', 'testnet3', 'testnet4', 'scalenet', 'chipnet', 'regtest'])
    .catch('mainnet'),
  // Same flag BCHN/Flowee clear on network switch (for dependents).
  fullySynced: z.boolean().catch(false),
  ipcEnabled: z.boolean().catch(true),
  utxozEnabled: z.boolean().catch(true),
  torEnabled: z.boolean().catch(false),
  // TEST ONLY default: the chipnet JD node's own tag.
  coinbaseTag: z.string().catch('/CyberAshven Knuth/'),
  // P2Pool v2 tag (docs/stratum.md of P2Pool, The coinbase tag): chain code and operator line.
  p2poolChain: z.string().catch(''),
  p2poolOperator: z.string().catch(''),
  rpcEnabled: z.boolean().catch(false),
  rpcUser: z.string().catch(''),
  rpcPassword: z.string().catch(''),
})

export const storeJson = FileHelper.json(
  {
    base: sdk.volumes.main,
    subpath: 'store.json',
  },
  shape,
)
