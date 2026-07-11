import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_1_0_1 = VersionInfo.of({
  version: '1.1.0:1',
  releaseNotes:
    'Now pulls binary directly from the official upstream GHCR image ' +
    '(ghcr.io/k-nuth/kth). Local build removed — no functional change.',
  migrations: {
    up: async ({ effects }) => {},
    down: async ({ effects }) => {},
  },
})
