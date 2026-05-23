import { sdk } from '../sdk'
import { configure } from './configure'
import { autoconfig } from './config/autoconfig'
import { networkConfig } from './config/network'
import { runtimeInfo } from './runtimeInfo'
import { rpcCredentialsSoon } from './rpcCredentialsSoon'
import { deletePeerList } from './deletePeerList'
import { deleteTestNetworkData } from './deleteTestNetworkData'

export const actions = sdk.Actions.of()
  // ── Hidden (cross-package) ──────────────────────────────────────────────────
  .addAction(autoconfig)
  // ── Info ────────────────────────────────────────────────────────────────────
  .addAction(runtimeInfo)
  // ── Configuration ───────────────────────────────────────────────────────────
  .addAction(networkConfig)
  .addAction(configure)
  // ── Credentials ─────────────────────────────────────────────────────────────
  .addAction(rpcCredentialsSoon)
  // ── Maintenance ─────────────────────────────────────────────────────────────
  .addAction(deletePeerList)
  .addAction(deleteTestNetworkData)
