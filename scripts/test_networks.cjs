const assert = require('node:assert/strict')
const fs = require('node:fs')
const test = require('node:test')
const ts = require('typescript')

// Load the delivered helpers and store schema with their real SDK, as in the
// config tests, so network changes are checked against the package itself.
require.extensions['.ts'] = (module, filename) => {
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  })
  module._compile(outputText, filename)
}

const {
  NETWORKS,
  networkPorts,
  networkName,
  networkDbDir,
  networkHostsFile,
} = require('../startos/utils.ts')
const { shape } = require('../startos/fileModels/store.json.ts')

const expected = {
  mainnet: { rpc: 8332, peer: 8333, name: 'mainnet', dir: '/data/blockchain', hosts: '/data/peers.dat' },
  testnet3: { rpc: 18332, peer: 18333, name: 'testnet', dir: '/data/testnet3', hosts: '/data/testnet3/peers.dat' },
  testnet4: { rpc: 28332, peer: 28333, name: 'testnet4', dir: '/data/testnet4', hosts: '/data/testnet4/peers.dat' },
  scalenet: { rpc: 38332, peer: 38333, name: 'scalenet', dir: '/data/scalenet', hosts: '/data/scalenet/peers.dat' },
  chipnet: { rpc: 48332, peer: 48333, name: 'chipnet', dir: '/data/chipnet', hosts: '/data/chipnet/peers.dat' },
  tempnet: { rpc: 48332, peer: 48333, name: 'tempnet', dir: '/data/tempnet', hosts: '/data/tempnet/peers-tempnet.dat' },
  regtest: { rpc: 18443, peer: 18444, name: 'regtest', dir: '/data/regtest', hosts: '/data/regtest/peers.dat' },
}

test('network order and store options include Tempnet immediately after Chipnet', () => {
  assert.deepEqual(NETWORKS, Object.keys(expected))
  assert.equal(shape.parse({}).network, 'mainnet')
  assert.equal(shape.parse({ network: 'unknown' }).network, 'mainnet')
})

for (const [network, { rpc, peer, name, dir, hosts }] of Object.entries(expected)) {
  test(`${network} keeps its ports, command name and isolated data paths`, () => {
    assert.equal(shape.parse({ network }).network, network)
    assert.deepEqual(networkPorts[network], { rpc, peer })
    assert.equal(networkName[network], name)
    assert.equal(networkDbDir(network), dir)
    assert.equal(networkHostsFile(network), hosts)
  })
}

test('Tempnet shares Chipnet ports while keeping separate chain and peer data', () => {
  assert.deepEqual(networkPorts.tempnet, networkPorts.chipnet)
  assert.notEqual(networkDbDir('tempnet'), networkDbDir('chipnet'))
  assert.notEqual(networkHostsFile('tempnet'), networkHostsFile('chipnet'))
})
