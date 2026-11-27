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

// Package versions before 1.7.0:2 wrote two policy values into kth.cfg whether
// or not the operator chose them, both stricter than Knuth's own BCH defaults:
//
// - node.sigop_fee_satoshis = 100, Knuth's value for BTC. Knuth's BCH default
//   is 0, as BCH bounds signature work with the SigChecks limits and BCHN
//   charges no fee per sigop. With 100 the node refused ordinary 1 sat/byte
//   transactions that BCHN relays, so block templates built from Knuth left
//   them out.
// - node.minimum_output_satoshis = 546. Knuth's BCH default is 0: no output
//   value is refused as dust. The flat 546 also refused outputs BCHN takes,
//   as BCHN sets the dust limit by the size of the output (540 satoshis for a
//   P2SH output).
//
// An update from one of those versions sets a stored old default to Knuth's
// default and keeps any other value, which the operator chose. Updates from
// 1.7.0:2 on leave the values alone, so an old default set again later stays.
const policyDefaultsWereStricter = VersionRange.parse('<1.7.0:2')

/**
 * The value an update writes, or undefined to keep the stored one: `to` when
 * `stored` is the old package default `was` and the update starts from a
 * version before 1.7.0:2. `from` is the data version the update starts from.
 */
function oldDefaultAfterUpdate(
  stored: number | undefined,
  was: number,
  to: number,
  from: string | null,
): number | undefined {
  if (stored !== was || !from) return undefined
  try {
    return ExtendedVersion.parse(from).satisfies(policyDefaultsWereStricter)
      ? to
      : undefined
  } catch {
    // Not a single version: nothing says the value is the old default.
    return undefined
  }
}

/** The sigop fee an update writes, or undefined to keep the stored one. */
export function sigopFeeAfterUpdate(
  stored: number | undefined,
  from: string | null,
): number | undefined {
  return oldDefaultAfterUpdate(stored, 100, 0, from)
}

/** The dust threshold an update writes, or undefined to keep the stored one. */
export function dustThresholdAfterUpdate(
  stored: number | undefined,
  from: string | null,
): number | undefined {
  return oldDefaultAfterUpdate(stored, 546, 0, from)
}

export const current = VersionInfo.of({
  version: '1.7.0:1.2',
  releaseNotes:
    'TEST ONLY: package 1.7.0:1 with kth-jd built from CyberAshven/kth 7856e9b8 ' +
    '(the 8582780e of 1.7.0:1.1 merged with k-nuth/kth#1005, which brings #993: ' +
    'Job Declaration upstreams through Tor and I2P and a latency limit by route), ' +
    'reported as 1.7.0, and Node Settings -> Coinbase Tag. ' +
    'The Sigop Fee and the Dust Threshold default to 0, the BCH defaults of ' +
    'Knuth itself, instead of 100 and 546. Updating sets a stored 100 sigop ' +
    'fee and a stored 546 dust threshold to 0; any other value is kept.',
  migrations: {
    up: async ({ effects }) => {
      const conf = await knuthConf.read().once()
      const from = await effects.getDataVersion()
      const fee = sigopFeeAfterUpdate(conf?.['node.sigop_fee_satoshis'], from)
      const dust = dustThresholdAfterUpdate(
        conf?.['node.minimum_output_satoshis'],
        from,
      )
      const changes: {
        'node.sigop_fee_satoshis'?: number
        'node.minimum_output_satoshis'?: number
      } = {}
      if (fee !== undefined) changes['node.sigop_fee_satoshis'] = fee
      if (dust !== undefined) changes['node.minimum_output_satoshis'] = dust
      if (Object.keys(changes).length > 0) {
        await knuthConf.merge(effects, changes)
      }
    },
    down: ALLOW_DOWNGRADE ? async () => {} : IMPOSSIBLE,
  },
})
