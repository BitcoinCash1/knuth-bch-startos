const assert = require('node:assert/strict')
const fs = require('node:fs')
const test = require('node:test')
const ts = require('typescript')

// Load the delivered config schema with its real SDK, without bundling a
// separate copy of the coercion logic into this test.
require.extensions['.ts'] = (module, filename) => {
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  })
  module._compile(outputText, filename)
}

const { shape } = require('../startos/fileModels/knuth.conf.ts')

test('numeric INI values retain ordinary finite values', () => {
  const parsed = shape.parse({
    'net.inbound_port': '48333',
    'node.byte_fee_satoshis': '0.25',
    'node.sigop_fee_satoshis': 100,
  })
  assert.equal(parsed['net.inbound_port'], 48333)
  assert.equal(parsed['node.byte_fee_satoshis'], 0.25)
  assert.equal(parsed['node.sigop_fee_satoshis'], 100)
})

for (const invalid of ['abc', 'Infinity', '-Infinity', '1e999', NaN, Infinity, -Infinity]) {
  test(`invalid numeric INI value ${String(invalid)} uses declared defaults`, () => {
    const parsed = shape.parse({
      'net.inbound_port': invalid,
      'node.byte_fee_satoshis': invalid,
      'node.sigop_fee_satoshis': invalid,
    })
    assert.equal(parsed['net.inbound_port'], 8333)
    assert.equal(parsed['node.byte_fee_satoshis'], 1)
    assert.equal(parsed['node.sigop_fee_satoshis'], 100)
  })
}
