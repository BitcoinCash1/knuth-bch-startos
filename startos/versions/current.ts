import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

export const current = VersionInfo.of({
  version: '1.3.0:7',
  releaseNotes: {
    en_US:
      'Packaging update for Knuth 1.3.0 (the node itself is unchanged): the package now follows the current StartOS package template and is built and released the same way as the other Bitcoin Cash packages. Downgrading from this release is not supported.',
    es_ES:
      'Actualización del paquete de Knuth 1.3.0 (el nodo no cambia): el paquete sigue ahora la plantilla actual de paquetes de StartOS y se compila y publica igual que los demás paquetes de Bitcoin Cash. No se admite volver a una versión anterior desde esta.',
    de_DE:
      'Paket-Update für Knuth 1.3.0 (der Knoten selbst ist unverändert): Das Paket folgt jetzt der aktuellen StartOS-Paketvorlage und wird wie die anderen Bitcoin-Cash-Pakete gebaut und veröffentlicht. Ein Downgrade von dieser Version wird nicht unterstützt.',
    pl_PL:
      'Aktualizacja pakietu Knuth 1.3.0 (sam węzeł bez zmian): pakiet korzysta teraz z aktualnego szablonu pakietów StartOS i jest budowany oraz wydawany tak samo jak pozostałe pakiety Bitcoin Cash. Powrót do starszej wersji z tego wydania nie jest obsługiwany.',
    fr_FR:
      "Mise à jour du paquet Knuth 1.3.0 (le nœud lui-même est inchangé) : le paquet suit désormais le modèle actuel de paquet StartOS et est compilé et publié comme les autres paquets Bitcoin Cash. Revenir à une version antérieure depuis celle-ci n'est pas pris en charge.",
  },
  migrations: {
    up: async ({ effects }) => {},
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
