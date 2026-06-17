import { VersionGraph } from '@start9labs/start-sdk'
import { v_0_85_0_0 } from './v0.85.0.0'
import { v_0_84_0_0 } from './v0.84.0.0'
import { v_0_83_0_3 } from './v0.83.0.3'
import { v_0_83_0_2 } from './v0.83.0.2'
import { v_0_83_0_1 } from './v0.83.0.1'
import { v_0_83_0_0 } from './v0.83.0.0'
import { v_0_79_0_0 } from './v0.79.0.0'
import { v_0_42_0_0 } from './v0.42.0.0'

export const versionGraph = VersionGraph.of({
  current: v_0_85_0_0,
  other: [v_0_84_0_0, v_0_83_0_3, v_0_83_0_2, v_0_83_0_1, v_0_83_0_0, v_0_79_0_0, v_0_42_0_0],
})
