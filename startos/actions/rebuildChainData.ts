import { sdk } from '../sdk'
import { mainMounts } from '../mounts'
import { networkDbDir, Network } from '../utils'
import { storeJson } from '../file-models/store.json'

// BCHN offers "Reindex Blockchain" (re-verify from existing blocks) and
// "Reindex Chainstate" (rebuild the UTXO set without re-downloading). kth v1.3.0
// has neither — its only chain-init flag is `--initchain`, which builds a fresh
// database. So the honest equivalent is a destructive rebuild that re-downloads,
// and the copy says so rather than implying an in-place reindex.
export const rebuildChainData = sdk.Action.withoutInput(
  'rebuild-chain-data',

  async ({ effects: _effects }) => ({
    name: 'Rebuild Blockchain Database',
    description:
      'Delete the blockchain, UTXO-Z and block-store databases for the active network so the node rebuilds them from scratch. Use this if the database is corrupted. Knuth has no in-place reindex, so this re-downloads the chain.',
    warning:
      'This deletes all chain data for the ACTIVE network and triggers a full re-sync, which can take hours. Peer list and RPC credentials are preserved.',
    allowedStatuses: 'only-stopped' as const,
    group: 'Maintenance',
    visibility: 'enabled' as const,
  }),

  async ({ effects }) => {
    const store = await storeJson.read().once()
    const network: Network = store?.network ?? 'mainnet'
    const dir = networkDbDir(network)

    await sdk.SubContainer.withTemp(
      effects,
      { imageId: 'knuth' },
      mainMounts,
      'rebuild-chain-data',
      async (sub) => {
        // Remove the chain databases but keep the directory and peers.dat,
        // which lives outside these subpaths.
        await sub.exec(['sh', '-c', `rm -rf ${dir}/utxoz ${dir}/blocks ${dir}/*.ldb ${dir}/header_index ${dir}/mempool.dat || true`])
      },
    )

    return {
      version: '1' as const,
      title: 'Blockchain Database Deleted',
      message: `Chain data for ${network} removed from ${dir}. Start the node to rebuild it — this performs a full re-sync.`,
      result: null,
    }
  },
)
