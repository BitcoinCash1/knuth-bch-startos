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
const { sigopFeeAfterUpdate } = require('../startos/versions/current.ts')

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
    assert.equal(parsed['node.sigop_fee_satoshis'], 0)
  })
}

test("a missing sigop fee is no fee, kth's BCH default", () => {
  assert.equal(shape.parse({})['node.sigop_fee_satoshis'], 0)
})

// Versions before 1.7.0:2 stored 100 as the package default.
for (const from of ['1.7.0:1', '1.7.0:1.1', '1.5.2:1', '1.3.0:5']) {
  test(`an update from ${from} sets the old default 100 to 0`, () => {
    assert.equal(sigopFeeAfterUpdate(100, from), 0)
  })
}

test('an update keeps a sigop fee the operator chose', () => {
  for (const stored of [0, 1, 50, 99.5, 101, 1000]) {
    assert.equal(sigopFeeAfterUpdate(stored, '1.7.0:1'), undefined)
  }
  assert.equal(sigopFeeAfterUpdate(undefined, '1.7.0:1'), undefined)
})

test('an update from 1.7.0:2 or later keeps 100, set after the default changed', () => {
  for (const from of ['1.7.0:2', '1.7.0:3', '1.8.0:0']) {
    assert.equal(sigopFeeAfterUpdate(100, from), undefined)
  }
})

test('without a single data version the sigop fee is kept', () => {
  assert.equal(sigopFeeAfterUpdate(100, null), undefined)
  assert.equal(sigopFeeAfterUpdate(100, 'not a version'), undefined)
})
