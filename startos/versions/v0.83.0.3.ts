import { VersionInfo } from '@start9labs/start-sdk'

export const v_0_83_0_3 = VersionInfo.of({
  version: '0.83.0:3',
  releaseNotes: 'Show active network (Mainnet/Chipnet/etc.) in node health check.',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
