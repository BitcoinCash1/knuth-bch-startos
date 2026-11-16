#!/usr/bin/env node
// TEST ONLY. The BCH Explorer's 'p2p' backend (CyberAshven/bitcoin-cash-explorer,
// feat/p2p-light-client) as a light client of kth: it syncs kth's script
// filters (type 1) over P2P into its own filter store, then reads address
// histories from them, in block mode (matching blocks downloaded whole, no
// getcfrange or getcfproof) and in range mode (getcfrange and getcfproof).
// Every history, funded and spent sum and set of unspent outputs is compared
// with BCHN's own reading of every block over RPC.
//
// As the explorer runs with SPAWN_CLUSTER_PROCS above 0, two worker processes
// read the filter store this process writes (FilterReader) and give the same
// histories, before and after a new block. The first lookup is timed with the
// native filter scan beside the TypeScript matcher it replaced, and both must
// find the same blocks. A transaction whose outputs are all spent is looked up
// by its txid alone from the filters' transaction leaves (kind 4), when kth's
// filters have them; otherwise that check is skipped and says so.
//
// Run by knuth_rpc_e2e.py (row 1008). Environment:
//   EXPLORER_BACKEND  the explorer's backend directory, built (dist/api/bitcoin/p2p/*.js)
//   KTH_P2P           kth's P2P host:port (default 127.0.0.1:18555)
//   KTH_RPC           kth's RPC host:port, to wait for kth to take a new block (optional)
//   BCHN_RPC          BCHN's RPC host:port (default 127.0.0.1:18443)
//   RPC_AUTH          user:password of both RPCs (default e2e:e2e)
//   TARGETS           JSON [{"name": ..., "address": ...} or {"name": ..., "script": hex}]
//   MINE_TO           an address: BCHN mines one more block to it, which kth and
//                     the explorer must follow (empty: no new block)
//   OUT_DIR           where the filter store and the log go
//   WORKERS           how many reader processes (default 2)
// Prints one PASS, FAIL or SKIP line per check, and last a line "SUMMARY {json}".
// Exits 0 when every check passed (skipped checks do not fail the run).
'use strict';

const childProcess = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const BACKEND = process.env.EXPLORER_BACKEND;
const [P2P_HOST, P2P_PORT] = (process.env.KTH_P2P || '127.0.0.1:18555').split(':');
const KTH_RPC = process.env.KTH_RPC || '';
const BCHN_RPC = process.env.BCHN_RPC || '127.0.0.1:18443';
const AUTH = 'Basic ' + Buffer.from(process.env.RPC_AUTH || 'e2e:e2e').toString('base64');
const TARGETS = JSON.parse(process.env.TARGETS || '[]');
const MINE_TO = process.env.MINE_TO || '';
const OUT_DIR = process.env.OUT_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'explorer-p2p-'));
const WORKERS = Number(process.env.WORKERS || 2);
const IS_WORKER = process.env.EXPLORER_P2P_WORKER === '1';
const TIMEOUT = 30_000;

const results = [];
const skipped = [];
const knuth = {};
const counts = {};
const timings = {};
function check(name, ok, seen) {
  results.push({ name, ok: !!ok });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${seen === undefined ? '' : ' ' + JSON.stringify(seen)}`);
  return !!ok;
}
function skip(name, reason) {
  skipped.push({ name, reason });
  console.log(`SKIP ${name} ${JSON.stringify({ reason })}`);
}
function finish(code) {
  const failed = results.filter((r) => !r.ok).map((r) => r.name);
  const summary = {
    ok: code === 0 && failed.length === 0 && results.length > 0,
    passed: results.length - failed.length,
    failed,
    skipped,
    knuth,
    counts,
    timings,
  };
  console.log('SUMMARY ' + JSON.stringify(summary));
  process.exit(summary.ok ? 0 : 1);
}

if (!BACKEND || !fs.existsSync(path.join(BACKEND, 'dist/api/bitcoin/p2p/light-client.js'))) {
  check('the explorer backend is built', false, { EXPLORER_BACKEND: BACKEND || '(unset)' });
  finish(2);
}
// The explorer's logger reads its config at load; a minimal one, logging to stdout only.
// Absolute, since the explorer's config.js require()s it and would read a
// relative path as a module name.
const configFile = path.resolve(OUT_DIR, 'explorer-config.json');
fs.mkdirSync(OUT_DIR, { recursive: true });
if (!IS_WORKER) {
  fs.writeFileSync(configFile, JSON.stringify({ SYSLOG: { ENABLED: false }, EXPLORER: { STDOUT_LOG_MIN_PRIORITY: 'info' } }));
}
process.env.EXPLORER_CONFIG_FILE = configFile;
const p2p = (m) => require(path.join(BACKEND, 'dist/api/bitcoin/p2p', m));
const { P2PPeer, P2PTimeoutError } = p2p('peer.js');
const { FilterReader, FilterStore } = p2p('filter-store.js');
const { LightClient, summarize } = p2p('light-client.js');
const { gcsMatch } = p2p('gcs.js');
const { txKey } = p2p('script-filter.js');
const { NETWORKS, NODE_CF, ZERO_HASH, sha256, toDisplay } = p2p('wire.js');
const { MSG_BLOCK, decodeHeaders, decodeNotFoundBlocks, encodeGetData, encodeGetHeaders } = p2p('messages.js');
const STORE_DIR = path.join(OUT_DIR, 'filters');

let rpcId = 0;
async function rpc(hostPort, method, ...params) {
  const res = await fetch(`http://${hostPort}/`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: AUTH },
    body: JSON.stringify({ id: ++rpcId, method, params }),
  });
  const body = await res.json();
  if (body.error) {
    throw new Error(`${method}: ${JSON.stringify(body.error)}`);
  }
  return body.result;
}
const bchn = (method, ...params) => rpc(BCHN_RPC, method, ...params);

async function until(what, fn, ms) {
  const end = Date.now() + ms;
  for (;;) {
    let last;
    try {
      if (await fn()) {
        return true;
      }
    } catch (e) {
      last = e;
    }
    if (Date.now() > end) {
      console.log(`waited ${ms} ms for ${what}${last ? ': ' + last.message : ''}`);
      return false;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
}

function newPeer(name) {
  return new P2PPeer({ host: P2P_HOST, port: Number(P2P_PORT), magic: NETWORKS.regtest.magic, timeout: TIMEOUT, name });
}

/** Wraps a peer's requests to count them by command. */
function counted(peer, label) {
  const sent = (counts[label] = {});
  const request = peer.request.bind(peer);
  peer.request = (command, ...rest) => {
    sent[command] = (sent[command] || 0) + 1;
    return request(command, ...rest);
  };
  return sent;
}

/** What kth answers to one request, in words for the log: the value, or what happened instead. */
async function ask(peer, command, payload, collect, ms = 20_000) {
  try {
    if (!(await until(`the ${peer.name} connection`, () => peer.isReady, 30_000))) {
      return { answered: false, what: 'no P2P connection to kth' };
    }
    return { answered: true, value: await peer.request(command, payload, collect, ms) };
  } catch (e) {
    return {
      answered: false,
      what: e instanceof P2PTimeoutError ? `no answer within ${ms} ms (connection dropped and made again)` : e.message,
    };
  }
}

/** BCHN's reading of a script's history: transactions, funded and spent sums, unspent outputs. */
function truth(blocks, scriptHex) {
  const created = new Map();
  const spent = new Set();
  const txs = [];
  let funded = 0;
  let spentSum = 0;
  for (const block of blocks) {
    const touched = new Set();
    // Outputs first: under canonical ordering a transaction may spend one created later in its block.
    block.tx.forEach((tx, i) => {
      for (const out of tx.vout) {
        if (out.scriptPubKey.hex === scriptHex) {
          const value = Math.round(out.value * 1e8);
          created.set(`${tx.txid}:${out.n}`, value);
          funded += value;
          touched.add(i);
        }
      }
    });
    block.tx.forEach((tx, i) => {
      for (const input of tx.vin) {
        const outpoint = `${input.txid}:${input.vout}`;
        if (!input.coinbase && created.has(outpoint)) {
          spent.add(outpoint);
          spentSum += created.get(outpoint);
          touched.add(i);
        }
      }
    });
    for (const i of [...touched].sort((a, b) => a - b)) {
      txs.push(`${block.height}:${i}:${block.tx[i].txid}`);
    }
  }
  const unspent = [...created.keys()].filter((o) => !spent.has(o)).sort();
  return { txs, funded, spent: spentSum, unspent };
}

function explorerView(history) {
  const s = summarize(history.txs);
  return {
    txs: history.txs.map((tx) => `${tx.height}:${tx.txIndex}:${tx.txid}`),
    funded: s.fundedSum,
    spent: s.spentSum,
    unspent: [...s.outputs.keys()].filter((o) => !s.spent.has(o)).sort(),
  };
}

async function readBchn(from, to, blocks) {
  for (let h = from; h <= to; h++) {
    blocks[h] = await bchn('getblock', await bchn('getblockhash', h), 2);
  }
}

/** The filter scan the explorer had before the native one: the store's files read in order, gcsMatch per filter. */
function typescriptScan(key, count) {
  const index = fs.readFileSync(path.join(STORE_DIR, 'index.dat'));
  const filters = fs.readFileSync(path.join(STORE_DIR, 'filters.dat'));
  const heights = [];
  let offset = 0;
  for (let h = 0; h < count; h++) {
    const length = index.readUInt32LE(h * 100 + 96);
    if (length > 32 && gcsMatch(filters.subarray(offset + 32, offset + length), index.subarray(h * 100, h * 100 + 32), key)) {
      heights.push(h);
    }
    offset += length;
  }
  return heights;
}

/**
 * A worker as a cluster worker of the explorer has it: a FilterReader of the
 * store the parent writes, its own P2P connection for blocks, and the
 * histories the parent asks for, once the reader has the height it names.
 */
async function runWorker() {
  const reader = new FilterReader(STORE_DIR, { network: 'regtest', pollInterval: 500 });
  const peer = newPeer(`worker ${process.pid} blocks`);
  const client = new LightClient(reader, peer, { mode: 'block', prefixBits: 256, maxHistoryTxs: 100000 });
  await reader.open();
  reader.start();
  peer.start();
  // Gone with the parent, as cluster workers are.
  process.on('disconnect', () => process.exit(0));
  process.send({ ready: true, tip: reader.tipHeight });
  process.on('message', async (message) => {
    if (message.cmd === 'stop') {
      peer.stop();
      await reader.stop();
      process.exit(0);
    }
    const t0 = Date.now();
    try {
      const reached = await until(
        `the reader to reach height ${message.tip}`,
        async () => {
          await client.refresh();
          return reader.synced && reader.tipHeight >= message.tip;
        },
        120_000
      );
      const views = {};
      for (const target of message.targets) {
        views[target.name] = explorerView(await client.history(sha256(Buffer.from(target.script, 'hex'))));
      }
      const location = message.txid ? await client.locateTx(Buffer.from(message.txid, 'hex').reverse()) : undefined;
      process.send({
        reached,
        tip: reader.tipHeight,
        tipHash: reader.tipHeight >= 0 ? toDisplay(reader.hashAt(reader.tipHeight)) : '',
        views,
        location,
        ms: Date.now() - t0,
      });
    } catch (e) {
      process.send({ error: e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : String(e) });
    }
  });
}

/** Worker processes forked before the store exists, as the explorer's cluster starts its workers. */
function startWorkers() {
  const workers = [];
  for (let i = 0; i < WORKERS; i++) {
    const child = childProcess.fork(__filename, [], { env: { ...process.env, EXPLORER_P2P_WORKER: '1' }, stdio: 'inherit' });
    const pending = [];
    child.on('message', (message) => pending.shift()?.(message));
    child.on('exit', (code) => {
      for (const resolve of pending.splice(0)) {
        resolve({ error: `worker exited with ${code}` });
      }
    });
    const next = () => new Promise((resolve) => pending.push(resolve));
    const ready = next();
    workers.push({
      child,
      ready,
      ask: (message) => {
        const answer = next();
        child.send(message);
        return answer;
      },
    });
  }
  return workers;
}

async function main() {
  const tip = await bchn('getblockcount');
  const genesis = Buffer.from(await bchn('getblockhash', 0), 'hex').reverse();
  console.log(`BCHN at ${tip}; kth P2P ${P2P_HOST}:${P2P_PORT}; explorer backend ${BACKEND}`);

  // What kth does with the requests the explorer's filter store depends on, asked one by one.
  const probe = newPeer('probe');
  probe.start();
  const headersAfterUnknown = await ask(probe, 'getheaders', encodeGetHeaders([ZERO_HASH]), (m) =>
    m.command === 'headers' ? { value: decodeHeaders(m.payload) } : false
  );
  knuth.remote = probe.remote && { agent: probe.remote.userAgent, height: probe.remote.startHeight };
  knuth.advertisesCF = !!probe.remote && (probe.remote.services & NODE_CF) !== 0n;
  knuth.getheadersUnknownLocator = headersAfterUnknown.answered
    ? {
        headers: headersAfterUnknown.value.length,
        fromGenesis: headersAfterUnknown.value.length > 0 && headersAfterUnknown.value[0].prevHash.equals(genesis),
      }
    : headersAfterUnknown.what;
  check(
    'kth answers getheaders with an unknown locator from its genesis block (how the store starts)',
    headersAfterUnknown.answered && knuth.getheadersUnknownLocator.fromGenesis,
    knuth.getheadersUnknownLocator
  );
  const genesisHeader = await ask(probe, 'getheaders', encodeGetHeaders([], genesis), (m) =>
    m.command === 'headers' ? { value: decodeHeaders(m.payload) } : false
  );
  knuth.getheadersEmptyLocator = genesisHeader.answered
    ? { headers: genesisHeader.value.length, isGenesis: genesisHeader.value.length === 1 && genesisHeader.value[0].hash.equals(genesis) }
    : genesisHeader.what;
  check(
    'kth answers getheaders with an empty locator with the stop block header alone (the genesis header)',
    genesisHeader.answered && knuth.getheadersEmptyLocator.isGenesis,
    knuth.getheadersEmptyLocator
  );
  const askBlock = (hash) =>
    ask(probe, 'getdata', encodeGetData([{ type: MSG_BLOCK, hash }]), (m) => {
      if (m.command === 'block') {
        return { value: 'block' };
      }
      if (m.command === 'notfound' && decodeNotFoundBlocks(m.payload).some((h) => h.equals(hash))) {
        return { value: 'notfound' };
      }
      return false;
    });
  // Block mode downloads blocks the filters match, never the genesis block,
  // which every client already has; so block 1 is the check, and what kth
  // answers for the genesis block is only recorded.
  const blockOne = Buffer.from(await bchn('getblockhash', 1), 'hex').reverse();
  const firstBlock = await askBlock(blockOne);
  knuth.getdataBlock = firstBlock.answered ? firstBlock.value : firstBlock.what;
  check('kth serves a block to getdata (block mode downloads matching blocks)', firstBlock.value === 'block', knuth.getdataBlock);
  const genesisBlock = await askBlock(genesis);
  knuth.getdataGenesisBlock = genesisBlock.answered ? genesisBlock.value : genesisBlock.what;
  probe.stop();

  // BCHN's own reading of every block.
  const blocks = [];
  await readBchn(0, tip, blocks);
  const targets = [];
  for (const target of TARGETS) {
    const script = target.script ?? (await bchn('validateaddress', target.address)).scriptPubKey;
    targets.push({ name: target.name, script });
  }
  targets.push({ name: 'an unused script', script: '76a914' + crypto.randomBytes(20).toString('hex') + '88ac' });

  // The readers start before the store exists, as cluster workers start beside the primary.
  const workers = WORKERS > 0 ? startWorkers() : [];
  const askWorkers = async (label, height, extraBlocks, located) => {
    const txid = located?.txid;
    const answers = await Promise.all(workers.map((w) => w.ask({ tip: height, targets, txid })));
    answers.forEach((answer, i) => {
      const expected = Object.fromEntries(targets.map((t) => [t.name, truth(extraBlocks, t.script)]));
      const same =
        !answer.error &&
        answer.reached &&
        answer.tipHash === extraBlocks[height].hash &&
        targets.every((t) => JSON.stringify(answer.views[t.name]) === JSON.stringify(expected[t.name])) &&
        (!txid || (answer.location?.blockHash === located.blockHash && answer.location?.txIndex === located.txIndex));
      check(
        `[worker ${i + 1}${label}] a reader of the filter store the parent writes reaches height ${height} and gives BCHN's histories` +
          (txid ? ' and the block of a transaction by its txid alone' : ''),
        same,
        answer.error
          ? { error: answer.error }
          : {
              tip: answer.tip,
              txs: Object.fromEntries(targets.map((t) => [t.name, answer.views[t.name]?.txs.length])),
              location: answer.location,
              ms: answer.ms,
              ...(same ? {} : { tipHash: answer.tipHash, bchnHash: extraBlocks[height].hash }),
            }
      );
    });
    return answers;
  };

  // The explorer's filter store, synced from kth; its peer also downloads blocks in block mode.
  const peer = newPeer('kth filters and blocks');
  const sentBlock = counted(peer, 'block mode and store');
  const store = new FilterStore(STORE_DIR, peer, { network: 'regtest', pollInterval: 2000 });
  const rangePeer = newPeer('kth ranges and proofs');
  const sentRange = counted(rangePeer, 'range mode');
  try {
    await store.open();
    peer.start();
    store.start();
    const t0 = Date.now();
    const synced = await until('the filter store to reach BCHN tip', () => store.synced && store.tipHeight >= tip, 300_000);
    if (
      !check('the filter store syncs every block of type 1 filters from kth', synced, {
        stored: store.tipHeight + 1,
        bchnTip: tip,
        ms: Date.now() - t0,
        lastError: store.lastError && store.lastError.message,
        requests: { ...sentBlock },
      })
    ) {
      return;
    }
    let same = true;
    for (let h = 0; h <= tip; h++) {
      if (toDisplay(store.hashAt(h)) !== blocks[h].hash || toDisplay(store.merkleRootAt(h)) !== blocks[h].merkleroot) {
        same = false;
        console.log(`kth's chain differs from BCHN's at height ${h}`);
        break;
      }
      await store.readFilter(h);
    }
    check("the stored block hashes and Merkle roots equal BCHN's, every filter chains to its header", same);

    // The first lookup's scan: the native addon over the store's files, beside the TypeScript matcher it replaced.
    const keys = targets.map((t) => sha256(Buffer.from(t.script, 'hex')));
    const scanStart = process.hrtime.bigint();
    const native = await store.match(keys, 0, store.tipHeight + 1);
    const nativeMs = Number(process.hrtime.bigint() - scanStart) / 1e6;
    const tsStart = process.hrtime.bigint();
    const typescript = keys.map((key) => typescriptScan(key, store.tipHeight + 1));
    const typescriptMs = Number(process.hrtime.bigint() - tsStart) / 1e6;
    timings.firstLookupScan = {
      blocks: store.tipHeight + 1,
      keys: keys.length,
      filterBytes: fs.statSync(path.join(STORE_DIR, 'filters.dat')).size,
      threads: os.availableParallelism(),
      nativeMs: +nativeMs.toFixed(2),
      typescriptMs: +typescriptMs.toFixed(2),
    };
    check(
      'the native filter scan finds the blocks the TypeScript matcher finds, for every target in one pass',
      JSON.stringify(native) === JSON.stringify(typescript),
      { ...timings.firstLookupScan, matches: native.map((h) => h.length) }
    );

    const clients = {
      block: new LightClient(store, peer, { mode: 'block', prefixBits: 256, maxHistoryTxs: 100000 }),
      range: new LightClient(store, rangePeer, { mode: 'range', prefixBits: 256, maxHistoryTxs: 100000 }),
    };
    rangePeer.start();
    const compare = async (mode, label, extraBlocks) => {
      const sent = mode === 'block' ? sentBlock : sentRange;
      for (const target of targets) {
        const before = { ...sent };
        const expected = truth(extraBlocks, target.script);
        let got;
        let error;
        const t = Date.now();
        try {
          got = explorerView(await clients[mode].history(sha256(Buffer.from(target.script, 'hex'))));
        } catch (e) {
          error = e instanceof P2PTimeoutError ? `kth did not answer: ${e.message}` : e.message;
        }
        const ms = Date.now() - t;
        if (!label && target === targets[0]) {
          timings[`firstLookup${mode === 'block' ? 'Block' : 'Range'}Mode`] = { target: target.name, ms };
        }
        const delta = Object.fromEntries(Object.keys(sent).map((k) => [k, sent[k] - (before[k] || 0)]).filter(([, v]) => v));
        const rangeOrProof = (delta.getcfrange || 0) + (delta.getcfproof || 0);
        check(
          `[${mode}${label}] ${target.name}: history, funded and spent sums and unspent outputs equal BCHN's` +
            (mode === 'block' ? ', with no getcfrange or getcfproof' : ''),
          !error && JSON.stringify(got) === JSON.stringify(expected) && (mode !== 'block' || rangeOrProof === 0),
          error
            ? { error, requests: delta }
            : {
                txs: got.txs.length,
                expectedTxs: expected.txs.length,
                funded: got.funded,
                spent: got.spent,
                unspent: got.unspent.length,
                ...(JSON.stringify(got) === JSON.stringify(expected) ? {} : { expected: { ...expected, txs: expected.txs.slice(0, 5) }, got: { ...got, txs: got.txs.slice(0, 5) } }),
                requests: delta,
                ms,
              }
        );
      }
    };
    await compare('block', '', blocks);
    await until('the range peer', () => rangePeer.isReady, 30_000);
    await compare('range', '', blocks);

    // A confirmed transaction whose outputs are all spent: the newest such, which kth cannot find by its id alone.
    const spentOutputs = new Set();
    for (const block of blocks) {
      for (const tx of block.tx) {
        for (const input of tx.vin) {
          if (!input.coinbase) {
            spentOutputs.add(`${input.txid}:${input.vout}`);
          }
        }
      }
    }
    let spentTx;
    for (let h = tip; h >= 1 && !spentTx; h--) {
      const tx = blocks[h].tx.find((t) => t.vout.length && t.vout.every((o) => spentOutputs.has(`${t.txid}:${o.n}`)));
      if (tx) {
        spentTx = { txid: tx.txid, height: h, blockHash: blocks[h].hash, txIndex: blocks[h].tx.indexOf(tx) };
      }
    }
    const txidName = 'a transaction with every output spent is found by its txid alone from the transaction leaves (kind 4)';
    if (!spentTx) {
      skip(txidName, 'no confirmed transaction of the chain has all its outputs spent');
    } else {
      // Whether kth's filters have transaction leaves: the filter of the transaction's block must hold its key.
      const filter = await store.readFilter(spentTx.height);
      const key = txKey(Buffer.from(spentTx.txid, 'hex').reverse());
      knuth.txLeaves = gcsMatch(filter.subarray(32), store.hashAt(spentTx.height), key);
      if (!knuth.txLeaves) {
        skip(
          txidName,
          `kth's filters have no transaction leaves (kind 4): the filter of block ${spentTx.height} does not hold the key of its transaction ${spentTx.txid}`
        );
      } else {
        for (const mode of ['block', 'range']) {
          const t = Date.now();
          let location;
          let error;
          try {
            location = await clients[mode].locateTx(Buffer.from(spentTx.txid, 'hex').reverse());
          } catch (e) {
            error = e.message;
          }
          let read;
          if (location && KTH_RPC) {
            read = await rpc(KTH_RPC, 'getrawtransaction', spentTx.txid, 2, location.blockHash).then(
              (tx) => tx.txid,
              (e) => `kth: ${e.message}`
            );
          }
          check(
            `[${mode}] ${txidName}, then read from kth with getrawtransaction <txid> 2 <blockhash>`,
            !error &&
              location &&
              location.height === spentTx.height &&
              location.blockHash === spentTx.blockHash &&
              location.txIndex === spentTx.txIndex &&
              (!KTH_RPC || read === spentTx.txid),
            { expected: spentTx, location, error, read, ms: Date.now() - t }
          );
        }
      }
    }

    if (workers.length) {
      await Promise.all(workers.map((w) => w.ready));
      await askWorkers('', tip, blocks, knuth.txLeaves ? spentTx : undefined);
    }

    // A new block: BCHN mines it, kth takes it from BCHN, the explorer follows kth.
    if (MINE_TO) {
      const [hash] = await bchn('generatetoaddress', 1, MINE_TO);
      const height = tip + 1;
      if (KTH_RPC) {
        const took = await until('kth to take the new block', async () => (await rpc(KTH_RPC, 'getblockcount')) >= height, 120_000);
        check("kth takes BCHN's new block", took, { height, hash });
      }
      const followed = await until('the filter store to follow kth', () => store.tipHeight >= height, 60_000);
      check('the explorer follows the new block from kth', followed && toDisplay(store.hashAt(height)) === hash, {
        stored: store.tipHeight,
        hash,
      });
      await readBchn(height, height, blocks);
      await compare('block', ', after the new block', blocks);
      await compare('range', ', after the new block', blocks);
      if (workers.length) {
        await askWorkers(', after the new block', height, blocks);
      }
    }
  } finally {
    for (const w of workers) {
      w.child.connected && w.child.send({ cmd: 'stop' });
    }
    rangePeer.stop();
    peer.stop();
    await store.stop().catch(() => undefined);
  }
}

if (IS_WORKER) {
  runWorker().catch((e) => {
    process.send?.({ error: e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : String(e) });
    process.exit(2);
  });
} else {
  main()
    .then(() => finish(0))
    .catch((e) => {
      check('the run completed', false, { error: e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : String(e) });
      finish(2);
    });
}
