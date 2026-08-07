import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_0_4 = VersionInfo.of({
  version: '1.3.0:4',
  releaseNotes:
    'Align network selection with BCHN (dropdown order, rpc/peer port table, ' +
    'fullySynced cleared on network switch). No user-facing network rename.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
