import { VersionGraph } from '@start9labs/start-sdk'
import { v_0_42_0_0 } from './v0.42.0.0'
import { v_0_79_0_0 } from './v0.79.0.0'

export const versionGraph = VersionGraph.of({
  current: v_0_79_0_0,
  other: [v_0_42_0_0],
})
