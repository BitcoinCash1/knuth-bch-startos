import {
  ExtendedVersion,
  IMPOSSIBLE,
  VersionInfo,
  VersionRange,
} from '@start9labs/start-sdk'
import { knuthConf } from '../fileModels/knuth.conf'

/**
 * Whether users may downgrade from this release to an earlier one. Set it per
 * release: `true` only when earlier versions can still read the data this one
 * leaves behind, `false` when this release is one-way.
 */
const ALLOW_DOWNGRADE = false

// Package versions before 1.7.0:2 wrote node.sigop_fee_satoshis = 100 into
// kth.cfg whether or not the operator chose it: 100 was the package default,
// which is Knuth's value for BTC. Knuth's BCH default is 0, as BCH bounds
// signature work with the SigChecks limits and BCHN charges no fee per sigop.
// With 100 the node refused ordinary 1 sat/byte transactions that BCHN relays,
// so block templates built from Knuth left them out. An update from one of
// those versions sets a stored 100 to 0 and keeps any other value, which the
// operator chose. Updates from 1.7.0:2 on leave the value alone, so 100 set
// again later stays.
const sigopFeeDefaultWas100 = VersionRange.parse('<1.7.0:2')

/**
 * The sigop fee an update writes, or undefined to keep the stored one.
 * `from` is the data version the update starts from.
 */
export function sigopFeeAfterUpdate(
  stored: number | undefined,
  from: string | null,
): number | undefined {
  if (stored !== 100 || !from) return undefined
  try {
    return ExtendedVersion.parse(from).satisfies(sigopFeeDefaultWas100)
      ? 0
      : undefined
  } catch {
    // Not a single version: nothing says the value is the old default.
    return undefined
  }
}

export const current = VersionInfo.of({
  version: '1.7.0:1.2',
  releaseNotes:
    'TEST ONLY: package 1.7.0:1 with kth-jd built from CyberAshven/kth 7856e9b8 ' +
    '(the 8582780e of 1.7.0:1.1 merged with k-nuth/kth#1005, which brings #993: ' +
    'Job Declaration upstreams through Tor and I2P and a latency limit by route), ' +
    'reported as 1.7.0, and Node Settings -> Coinbase Tag. ' +
    "The Sigop Fee defaults to 0, Knuth's own BCH default, instead of 100; " +
    'updating sets a stored 100 to 0 and keeps any other value.',
  migrations: {
    up: async ({ effects }) => {
      const conf = await knuthConf.read().once()
      const fee = sigopFeeAfterUpdate(
        conf?.['node.sigop_fee_satoshis'],
        await effects.getDataVersion(),
      )
      if (fee !== undefined) {
        await knuthConf.merge(effects, { 'node.sigop_fee_satoshis': fee })
      }
    },
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
