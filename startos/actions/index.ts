import { sdk } from '../sdk'
import { configure } from './configure'
import { autoconfig } from './config/autoconfig'
import { networkConfig } from './config/network'
import { mempoolPolicy } from './config/mempoolPolicy'
import { rpcPeersSettings } from './config/rpcPeersSettings'
import { runtimeInfo } from './runtimeInfo'
import { viewRpcCredentials } from './viewRpcCredentials'
import { generateRpcCredentials } from './generateRpcCredentials'
import { deleteRpcCredentials } from './deleteRpcCredentials'
import { deletePeerList } from './deletePeerList'
import { deleteTestNetworkData } from './deleteTestNetworkData'
import { rebuildChainData } from './rebuildChainData'

// Group order mirrors BCHN/BCHD/Flowee so the Actions & Config page reads the
// same across every BCH node package.
export const actions = sdk.Actions.of()
  // ── Hidden (cross-package) ──────────────────────────────────────────────────
  .addAction(autoconfig)
  // ── Configuration ───────────────────────────────────────────────────────────
  .addAction(mempoolPolicy)
  .addAction(networkConfig)
  .addAction(configure)
  .addAction(rpcPeersSettings)
  // ── Credentials ─────────────────────────────────────────────────────────────
  .addAction(deleteRpcCredentials)
  .addAction(generateRpcCredentials)
  .addAction(viewRpcCredentials)
  // ── Maintenance ─────────────────────────────────────────────────────────────
  .addAction(deletePeerList)
  .addAction(deleteTestNetworkData)
  .addAction(rebuildChainData)
  // ── Other ───────────────────────────────────────────────────────────────────
  .addAction(runtimeInfo)
