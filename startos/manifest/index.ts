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
    long: 'Knuth is a high-performance Bitcoin Cash full node written in C++. It provides Bitcoin Core-compatible JSON-RPC, ZMQ notifications, IPC via C-API, and UTXO queries. Designed for maximum performance — ideal for miners and services that need fast block validation and low-latency RPC.',
  },
  volumes: ['main'],
  images: {
    knuth: {
      source: { dockerBuild: {} },
      arch: ['x86_64', 'aarch64'],
      emulateMissingAs: 'x86_64',
    },
  },
  alerts: {
    install:
      'Knuth is a high-performance BCH full node. Initial Block Download may take several hours. Knuth uses Bitcoin Core-compatible RPC — dependent packages (Fulcrum, Explorer, mining pools) work with it just like BCHN.',
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
