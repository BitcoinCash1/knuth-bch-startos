import { sdk } from './sdk'

export const { createBackup, restoreInit } = sdk.setupBackups(
  async () =>
    sdk.Backups.ofVolumes('main').setOptions({
      // Chain + peer data (BCHN-style layout). Config/credentials stay in backup.
      exclude: [
        '/blockchain',
        '/testnet3',
        '/testnet4',
        '/scalenet',
        '/chipnet',
        '/tempnet',
        '/regtest',
        '/peers.dat',
        '/hosts-tempnet.cache',
        '/banlist-tempnet.dat',
        '/peers-tempnet.dat',
        '/debug.log',
        '/error.log',
      ],
    }),
)
