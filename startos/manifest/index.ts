import { setupManifest } from '@start9labs/start-sdk'
import { long, short } from './i18n'

export const manifest = setupManifest({
  id: 'knuth-bch',
  title: 'Knuth',
  license: 'MIT',
  packageRepo: 'https://github.com/BitcoinCash1/knuth-bch-startos',
  upstreamRepo: 'https://github.com/k-nuth/kth',
  marketingUrl: 'https://kth.cash',
  donationUrl: null,
  description: { short, long },
  volumes: ['main'],
  images: {
    knuth: {
      source: { dockerBuild: {} },
      arch: ['x86_64'],
      emulateMissing: true,
    },
  },
})
