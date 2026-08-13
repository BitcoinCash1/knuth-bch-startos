import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_4_0 = VersionInfo.of({
  version: '1.3.4:0',
  releaseNotes:
    'Build against @start9labs/start-sdk 2.0.9 (same as Start9-Community). Drop the removed manifest alerts field and use the 2.0 task input accept/set shape. TypeScript 6 + the SDK tsconfig base.',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
