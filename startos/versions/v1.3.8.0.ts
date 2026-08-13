import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_8_0 = VersionInfo.of({
  version: '1.3.8:0',
  releaseNotes:
    'EloPool/ckpool GBT: terminate JSON-RPC bodies with a newline. ckpool ' +
    'reads lines (not Content-Length) and treated a missing LF as a 20s hang ' +
    '("No bitcoinds active"). Also raise the sidecar accept backlog.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
