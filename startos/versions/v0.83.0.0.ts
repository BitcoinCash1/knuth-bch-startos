import { VersionInfo } from '@start9labs/start-sdk'

export const v_0_83_0_0 = VersionInfo.of({
  version: '0.83.0:0',
  releaseNotes:
    'Update to upstream Knuth v0.83.0 release. ' +
    'Adds instructions.',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
