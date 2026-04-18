import { setupManifest } from '@start9labs/start-sdk'

export const manifest = setupManifest({
  id: 'knuth-bch',
  title: 'Knuth',
  license: 'MIT',
  packageRepo: 'https://github.com/BitcoinCash1/knuth-bch-startos',
  upstreamRepo: 'https://github.com/k-nuth/kth',
  marketingUrl: 'https://kth.cash',
  donationUrl: null,
  docsUrls: [
    'https://github.com/BitcoinCash1/knuth-bch-startos/blob/master/README.md',
    'https://github.com/k-nuth/kth',
  ],
  description: {
    short: 'Knuth — High-performance C++ BCH full node',
    long: 'Knuth is a high-performance Bitcoin Cash full node written in C++. It validates blocks, relays transactions, and syncs the BCH blockchain. This package exposes node settings plus IPC/C-API and UTXOZ compatibility capabilities. JSON-RPC and gRPC are not exposed in this package version.',
  },
  volumes: ['main'],
  images: {
    knuth: {
      source: { dockerBuild: {} },
      arch: ['x86_64', 'aarch64', 'riscv64'],
      emulateMissingAs: 'x86_64',
    },
  },
  alerts: {
    install:
      'Knuth is a high-performance BCH full node. Initial Block Download may take several hours. Note: this version does not include RPC — it syncs and validates the blockchain only. RPC will be added in a future update.',
    update: null,
    uninstall:
      'Uninstalling will delete all blockchain data and configuration. A fresh sync will be required if you reinstall.',
    restore:
      'Restoring will overwrite current configuration. Blockchain data is not included in backups and will be re-synced automatically.',
    start: null,
    stop: null,
  },
  dependencies: {
    tor: {
      description:
        'Enables Tor onion routing for anonymous peer-to-peer connections.',
      optional: true,
      metadata: {
        title: 'Tor',
        icon: 'https://raw.githubusercontent.com/Start9Labs/tor-startos/65faea17febc739d910e8c26ff4e61f6333487a8/icon.svg',
      },
    },
  },
})
