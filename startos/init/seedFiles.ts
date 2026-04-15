import { sdk } from '../sdk'
import { knuthConf } from '../file-models/knuth.conf'
import { storeJson } from '../file-models/store.json'

export const seedFiles = sdk.setupOnInit(async (effects) => {
  // Seed default store on first install
  const existing = await storeJson.read().once()
  if (!existing) {
    await storeJson.merge(effects, {
      torEnabled: false,
    })
  }

  // Seed default config
  await knuthConf.merge(effects, {})
})
