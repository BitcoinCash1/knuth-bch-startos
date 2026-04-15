import { sdk } from '../sdk'
import { knuthConf } from '../file-models/knuth.conf'
import { storeJson } from '../file-models/store.json'

export const seedFiles = sdk.setupOnInit(async (effects) => {
  // Generate a random RPC password on first install
  const existing = await storeJson.read().once()
  if (!existing?.rpcPassword) {
    const chars =
      'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    let password = ''
    const bytes = new Uint8Array(32)
    globalThis.crypto.getRandomValues(bytes)
    for (const b of bytes) {
      password += chars[b % chars.length]
    }
    await storeJson.merge(effects, {
      rpcUser: 'knuth-bch',
      rpcPassword: password,
      torEnabled: false,
    })
  }

  // Seed default config
  await knuthConf.merge(effects, {})
})
