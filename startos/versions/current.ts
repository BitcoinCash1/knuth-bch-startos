import { FileHelper, IMPOSSIBLE, VersionInfo, z } from '@start9labs/start-sdk'
import { knuthConf } from '../fileModels/knuth.conf'
import { storeJson } from '../fileModels/store.json'
import { sdk } from '../sdk'
import { networkDbDir, networkHostsFile, networkPorts } from '../utils'

// The stored network as written, before the store's own schema turns a value it
// no longer knows into mainnet.
const storedNetwork = FileHelper.json(
  { base: sdk.volumes.main, subpath: 'store.json' },
  z.object({ network: z.string().catch('') }),
)

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

export const current = VersionInfo.of({
  version: '1.7.0:0',
  releaseNotes:
    'Upstream v1.7.0. Tempnet is removed: it ended on 15 November 2026, when its ' +
    'tests moved to chipnet. A node set to Tempnet is switched to chipnet, with ' +
    'its own chipnet data directory; the old /data/tempnet directory is left on disk.',
  migrations: {
    up: async ({ effects }) => {
      const stored = await storedNetwork.read().once()
      if (stored?.network !== 'tempnet') return
      // What the Network action does for a switch: chipnet's ports, chain
      // directory and peer file. Tempnet and chipnet share their ports.
      const { peer, rpc } = networkPorts.chipnet
      await storeJson.merge(effects, { network: 'chipnet', fullySynced: false })
      await knuthConf.merge(effects, {
        'net.inbound_port': peer,
        'net.hosts_file': networkHostsFile('chipnet'),
        'db.directory': networkDbDir('chipnet'),
        'rpc.port': rpc,
      })
    },
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
