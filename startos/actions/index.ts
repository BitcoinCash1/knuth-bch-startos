import { sdk } from '../sdk'
import { configure } from './configure'
import { autoconfig } from './config/autoconfig'

export const actions = sdk.Actions.of().addAction(configure).addAction(autoconfig)
