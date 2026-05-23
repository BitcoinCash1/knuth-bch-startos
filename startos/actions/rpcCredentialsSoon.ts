import { sdk } from '../sdk'

export const rpcCredentialsSoon = sdk.Action.withoutInput(
  'rpc-credentials',
  async ({ effects: _effects }) => ({
    name: 'RPC Credentials',
    description: 'Generate and manage RPC credentials for connecting wallets and services.',
    warning: null,
    allowedStatuses: 'any' as const,
    group: 'Credentials',
    visibility: { disabled: 'RPC support is coming in a future release.' } as const,
  }),
  async ({ effects: _effects }) => null,
)
