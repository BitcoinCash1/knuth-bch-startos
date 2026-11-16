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
        '/regtest',
        '/peers.dat',
        '/debug.log',
        '/error.log',
      ],
    }),
)
