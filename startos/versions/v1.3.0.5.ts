import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_0_5 = VersionInfo.of({
  version: '1.3.0:5',
  releaseNotes:
    'Actions & Config now mirrors BCHN/BCHD/Flowee: adds Mempool & Block Policy ' +
    'and RPC & Peers Settings, plus Generate and Delete RPC Credentials and a ' +
    'Rebuild Blockchain Database maintenance action. Health checks gain Peer ' +
    'Connections and I2P rows to match the other nodes, and two Knuth-specific ' +
    'rows: UTXO-Z Storage and IPC / C-API. Peer Connections reports disabled ' +
    'because Knuth v1.3.0 implements neither getpeerinfo nor getconnectioncount.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
