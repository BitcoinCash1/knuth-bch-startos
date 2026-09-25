import { setupManifest } from '@start9labs/start-sdk'
import { long, short, torDescription } from './i18n'

export const manifest = setupManifest({
  id: 'knuth-bch',
  title: 'Knuth',
  license: 'MIT',
  packageRepo: 'https://github.com/BitcoinCash1/knuth-bch-startos',
  upstreamRepo: 'https://github.com/k-nuth/kth',
  marketingUrl: 'https://kth.cash',
  donationUrl: null,
  docsUrls: [
    'https://github.com/BitcoinCash1/knuth-bch-startos/blob/master/instructions.md',
    'https://github.com/k-nuth/kth',
  ],
  description: { short, long },
  volumes: ['main'],
  images: {
    knuth: {
      source: { dockerBuild: {} },
      arch: ['x86_64', 'aarch64', 'riscv64'],
      emulateMissingAs: 'x86_64',
    },
  },
  dependencies: {
    tor: {
      description: torDescription,
      optional: true,
      metadata: {
        title: 'Tor',
        icon: 'https://raw.githubusercontent.com/Start9Labs/tor-startos/65faea17febc739d910e8c26ff4e61f6333487a8/icon.svg',
      },
    },
  },
})
