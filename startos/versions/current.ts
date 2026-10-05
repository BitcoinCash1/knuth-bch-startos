import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

export const current = VersionInfo.of({
  version: '1.3.0:6',
  releaseNotes:
    'Packaging revision of kth 1.3.0 (upstream version is unchanged). ' +
    'Health checks now match BCHN/BCHD/Flowee. A Bitcoin-RPC compatibility ' +
    'sidecar serves getblock/getrawtransaction/getblocktemplate from blk*.dat ' +
    'so Fulcrum, Explorer, and the pools can use Knuth. Blockchain Sync prefers ' +
    'the sidecar tip so a few-block native blocks/headers lag is Synced, not ' +
    '"Syncing 100%". The s9pk is packed for x86_64, aarch64 and riscv64 ' +
    '(non-x86 runs the amd64 image under StartOS qemu). Tor stays optional. ' +
    'The node now reasserts the active network\'s P2P port, data directory and ' +
    'hosts file on every start: kth tells chipnet from testnet4 only by port ' +
    '48333, so a port left over from another network ran testnet4 rules on ' +
    'chipnet. Rebuild Blockchain Database now removes the whole network ' +
    'directory, including the header index it used to leave behind.',
  migrations: {
    up: async () => {},
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
