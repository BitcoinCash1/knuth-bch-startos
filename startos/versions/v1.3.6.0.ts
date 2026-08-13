import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_6_0 = VersionInfo.of({
  version: '1.3.6:0',
  releaseNotes:
    'Unblock the RPC compatibility sidecar: the primary health check was ' +
    'probing the public RPC port that the sidecar itself serves, so the ' +
    'sidecar waited forever on primary. Health now probes kth on 127.0.0.1:19332.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
