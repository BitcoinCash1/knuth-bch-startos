import { FileHelper, VersionInfo, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'
import { knuthConf } from '../file-models/knuth.conf'

const iniNumber = z.union([z.string().transform(Number), z.number()])

// kth v1.3.0 (k-nuth/kth#515) renamed most config keys. kth calls
// allow_config_extras(), so the old names are ignored rather than rejected —
// the node would start fine and quietly run on its own defaults. Read the old
// keys off the existing kth.cfg and carry the user's values onto the new ones.
const legacyConf = FileHelper.ini(
  {
    base: sdk.volumes.main,
    subpath: 'kth.cfg',
  },
  z.object({
    'network.inbound_connections': iniNumber.optional(),
    'network.outbound_connections': iniNumber.optional(),
    'network.relay_transactions': z.boolean().optional(),
    'database.directory': z.string().optional(),
    'database.db_max_size': iniNumber.optional(),
    'database.safe_mode': z.boolean().optional(),
    'database.cache_capacity': iniNumber.optional(),
    'database.db_mode': z.enum(['full_indexed', 'normal', 'pruned']).optional(),
    'blockchain.cores': iniNumber.optional(),
  }),
)

export const v_1_3_0_1 = VersionInfo.of({
  version: '1.3.0:1',
  releaseNotes:
    'Knuth v1.3.0 adds a BCH mempool, block-template assembly and an optional ' +
    'JSON-RPC server. This release wires that up: a JSON-RPC toggle with ' +
    'generated credentials and a per-network RPC interface, replacing the ' +
    'placeholder "coming soon" action. Also realigns kth.cfg with the config ' +
    'keys renamed upstream in v1.3.0 (network.* -> net.*, database.* -> db.*, ' +
    'blockchain.* -> chain.*); existing settings are carried over automatically.',
  migrations: {
    up: async ({ effects }) => {
      const old = await legacyConf.read().once()
      if (!old) return

      const carried: Record<string, unknown> = {}
      const carry = (from: keyof typeof old, to: string) => {
        const v = old[from]
        if (v !== undefined) carried[to] = v
      }

      carry('network.inbound_connections', 'net.inbound_connections')
      carry('network.outbound_connections', 'net.outbound_connections')
      carry('network.relay_transactions', 'node.relay_transactions')
      carry('database.directory', 'db.directory')
      carry('database.db_max_size', 'db.db_max_size')
      carry('database.safe_mode', 'db.safe_mode')
      carry('database.cache_capacity', 'db.cache_capacity')
      carry('blockchain.cores', 'chain.cores')

      // db_mode's accepted *values* changed too, not just the key: v1.3.0 takes
      // pruned|blocks|full and rejects the old spellings outright with
      // "--db.db_mode: illegal value", which crash-loops the node on startup.
      const dbModeMap = {
        full_indexed: 'full',
        normal: 'blocks',
        pruned: 'pruned',
      } as const
      const oldMode = old['database.db_mode']
      if (oldMode !== undefined) carried['db.db_mode'] = dbModeMap[oldMode]

      if (Object.keys(carried).length > 0) {
        await knuthConf.merge(effects, carried as never)
      }
    },
    down: async ({ effects }) => {},
  },
})
