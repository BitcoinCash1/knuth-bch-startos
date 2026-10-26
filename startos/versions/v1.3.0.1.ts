import { FileHelper, IMPOSSIBLE, VersionInfo, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'
import { knuthConf } from '../fileModels/knuth.conf'

const iniNumber = z
  .union([z.string().transform(Number), z.number()])
  .pipe(z.number().finite())

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
  releaseNotes: {
    en_US:
      'Knuth 1.3.0 adds a Bitcoin Cash mempool, block-template assembly, and an optional JSON-RPC server. This release wires that up: a JSON-RPC toggle with generated credentials and a per-network RPC interface. Existing settings are carried over to the configuration keys upstream renamed in 1.3.0.',
    es_ES:
      'Knuth 1.3.0 añade una mempool de Bitcoin Cash, ensamblado de plantillas de bloque y un servidor JSON-RPC opcional. Esta versión lo integra: un interruptor JSON-RPC con credenciales generadas y una interfaz RPC por red. Los ajustes existentes se trasladan a las claves de configuración renombradas por el proyecto en 1.3.0.',
    de_DE:
      'Knuth 1.3.0 bringt einen Bitcoin-Cash-Mempool, Blockvorlagen-Erstellung und einen optionalen JSON-RPC-Server. Diese Version bindet das ein: einen JSON-RPC-Schalter mit erzeugten Zugangsdaten und eine RPC-Schnittstelle je Netzwerk. Bestehende Einstellungen werden auf die in 1.3.0 umbenannten Konfigurationsschlüssel übertragen.',
    pl_PL:
      'Knuth 1.3.0 dodaje mempool Bitcoin Cash, składanie szablonów bloków i opcjonalny serwer JSON-RPC. To wydanie je udostępnia: przełącznik JSON-RPC z generowanymi danymi logowania oraz interfejs RPC osobny dla każdej sieci. Istniejące ustawienia są przenoszone na klucze konfiguracyjne przemianowane w wersji 1.3.0.',
    fr_FR:
      "Knuth 1.3.0 ajoute un mempool Bitcoin Cash, l'assemblage de modèles de blocs et un serveur JSON-RPC facultatif. Cette version l'intègre : un interrupteur JSON-RPC avec identifiants générés et une interface RPC par réseau. Les réglages existants sont reportés sur les clés de configuration renommées en amont dans la 1.3.0.",
  },
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
    down: IMPOSSIBLE,
  },
})
