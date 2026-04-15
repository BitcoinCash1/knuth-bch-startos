import { FileHelper, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'

export const shape = z.object({
  network: z.enum(['mainnet']).catch('mainnet'),
  torEnabled: z.boolean().catch(false),
})

export const storeJson = FileHelper.json(
  {
    base: sdk.volumes.main,
    subpath: 'store.json',
  },
  shape,
)
