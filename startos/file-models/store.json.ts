import { FileHelper, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'

export const shape = z.object({
  rpcUser: z.string().catch('knuth-bch'),
  rpcPassword: z.string().catch(''),
  network: z.enum(['mainnet']).catch('mainnet'),
  fullySynced: z.boolean().catch(false),
  torEnabled: z.boolean().catch(false),
})

export const storeJson = FileHelper.json(
  {
    base: sdk.volumes.main,
    subpath: 'store.json',
  },
  shape,
)
