import { VersionInfo } from '@start9labs/start-sdk'

export const v_0_83_0_2 = VersionInfo.of({
  version: '0.83.0:2',
  releaseNotes: 'Fix network switch: auto-restart after network change via effects.restart().',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
