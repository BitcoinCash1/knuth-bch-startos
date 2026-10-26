import { i18n } from '../i18n'
import { sdk } from '../sdk'
import { storeJson } from '../fileModels/store.json'
import { mainMounts } from '../mounts'
import { rootDir, Network } from '../utils'

const { InputSpec, Value } = sdk

const inputSpec = InputSpec.of({
  networks: Value.multiselect({
    name: i18n('Networks To Delete'),
    description: i18n(
      'Test networks whose blockchain data should be deleted. Mainnet cannot be selected.',
    ),
    warning: i18n(
      'Block data and chainstate for the selected networks are permanently deleted.',
    ),
    default: [],
    minLength: 0,
    maxLength: null,
    values: {
      testnet3: i18n('Testnet3'),
      testnet4: i18n('Testnet4'),
      scalenet: i18n('Scalenet'),
      chipnet: i18n('Chipnet'),
      regtest: i18n('Regtest'),
    },
  }),
})

const testNetSubdirs: Record<string, string> = {
  testnet3: 'testnet3',
  testnet4: 'testnet4',
  scalenet: 'scalenet',
  chipnet: 'chipnet',
  regtest: 'regtest',
}

export const deleteTestNetworkData = sdk.Action.withInput(
  'delete-test-network-data',
  async ({ effects: _effects }) => ({
    name: i18n('Delete Test Network Data'),
    description: i18n(
      'Free disk space by deleting the blockchain data for one or more test networks. Mainnet is never affected.',
    ),
    warning: i18n(
      'Block data and chainstate for the selected networks are permanently deleted.',
    ),
    // Must be stopped: deleting the active network while kth is still
    // running just gets rewritten. Stop used to hang (5 min SIGTERM);
    // it now SIGKILLs after 45s so this action can actually run.
    allowedStatuses: 'only-stopped' as const,
    group: 'Maintenance',
    visibility: 'enabled' as const,
  }),
  inputSpec,
  async ({ effects: _effects }) => {
    const all: Array<
      'testnet3' | 'testnet4' | 'scalenet' | 'chipnet' | 'regtest'
    > = ['testnet3', 'testnet4', 'scalenet', 'chipnet', 'regtest']
    return { networks: all }
  },
  async ({ effects, input }) => {
    const networks = (input.networks ?? []).filter(Boolean) as string[]
    if (networks.length === 0) {
      return {
        version: '1' as const,
        title: i18n('Nothing to Delete'),
        message: i18n('No networks were selected.'),
        result: null,
      }
    }
    const store = await storeJson.read().once()
    const activeNetwork: Network = store?.network ?? 'mainnet'
    if (networks.includes(activeNetwork)) {
      await storeJson.merge(effects, { fullySynced: false })
    }
    await sdk.SubContainer.withTemp(
      effects,
      { imageId: 'knuth' },
      mainMounts,
      'delete-test-net-data',
      async (sub) => {
        for (const net of networks) {
          const subdir = testNetSubdirs[net]
          if (subdir)
            await sub.exec(
              ['rm', '-rf', `${rootDir}/${subdir}`],
              { timeout: null },
            )
        }
      },
    )
    return {
      version: '1' as const,
      title: i18n('Test Network Data Deleted'),
      message: i18n('Deleted chain data for: ${networks}.', {
        networks: networks.join(', '),
      }),
      result: null,
    }
  },
)
