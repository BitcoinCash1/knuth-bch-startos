import { VersionInfo } from '@start9labs/start-sdk'

export const v_1_3_3_0 = VersionInfo.of({
  version: '1.3.3:0',
  releaseNotes:
    'Tor always appears on the Dependencies tab as an optional package ' +
    '(installed / exists). Enabling Node Settings → Tor Routing promotes it to ' +
    'a running requirement. StartOS hides unused optional deps, so the previous ' +
    'empty currentDependencies list made Tor look missing.',
  migrations: {
    up: async () => {},
    down: async () => {},
  },
})
