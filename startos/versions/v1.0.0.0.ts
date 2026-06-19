import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_0_0_0 = VersionInfo.of({
  version: '1.0.0:0',
  releaseNotes: 'Upstream v1.0.0.',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
