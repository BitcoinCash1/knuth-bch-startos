import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_9_0 = VersionInfo.of({
  version: '1.3.9:0',
  releaseNotes:
    'Packaging bump of kth 1.3.0 (StartOS ignores revision-only sideloads). ' +
    'Blockchain Sync no longer sits on "Syncing 100%" when kth RPC blocks ' +
    'lag headers by a few at the tip — health prefers the sidecar tip and ' +
    'treats a <0.1% gap as Synced.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
