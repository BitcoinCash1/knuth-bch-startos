#!/usr/bin/env node
// TEST ONLY. The BCH Explorer's 'p2p' backend (CyberAshven/bitcoin-cash-explorer,
// feat/p2p-light-client) as a light client of kth: it syncs kth's script
// filters (type 1) over P2P into its own filter store, then reads address
// histories from them, in block mode (matching blocks downloaded whole, no
// getcfrange or getcfproof) and in range mode (getcfrange and getcfproof).
// Every history, funded and spent sum and set of unspent outputs is compared
// with BCHN's own reading of every block over RPC.
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
// Prints one PASS or FAIL line per check, and last a line "SUMMARY {json}".
// Exits 0 when every check passed.
'use strict';

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
const TIMEOUT = 30_000;

const results = [];
const knuth = {};
const counts = {};
function check(name, ok, seen) {
  results.push({ name, ok: !!ok });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${seen === undefined ? '' : ' ' + JSON.stringify(seen)}`);
  return !!ok;
}
function finish(code) {
  const failed = results.filter((r) => !r.ok).map((r) => r.name);
  const summary = { ok: code === 0 && failed.length === 0 && results.length > 0, passed: results.length - failed.length, failed, knuth, counts };
  console.log('SUMMARY ' + JSON.stringify(summary));
  process.exit(summary.ok ? 0 : 1);
}

if (!BACKEND || !fs.existsSync(path.join(BACKEND, 'dist/api/bitcoin/p2p/light-client.js'))) {
  check('the explorer backend is built', false, { EXPLORER_BACKEND: BACKEND || '(unset)' });
  finish(2);
}
// The explorer's logger reads its config at load; a minimal one, logging to stdout only.
const configFile = path.join(OUT_DIR, 'explorer-config.json');
fs.mkdirSync(OUT_DIR, { recursive: true });
fs.writeFileSync(configFile, JSON.stringify({ SYSLOG: { ENABLED: false }, EXPLORER: { STDOUT_LOG_MIN_PRIORITY: 'info' } }));
process.env.EXPLORER_CONFIG_FILE = configFile;
const p2p = (m) => require(path.join(BACKEND, 'dist/api/bitcoin/p2p', m));
const { P2PPeer, P2PTimeoutError } = p2p('peer.js');
const { FilterStore } = p2p('filter-store.js');
const { LightClient, summarize } = p2p('light-client.js');
const { NETWORKS, NODE_CF, ZERO_HASH, sha256, toDisplay } = p2p('wire.js');
const { MSG_BLOCK, decodeHeaders, decodeNotFoundBlocks, encodeGetData, encodeGetHeaders } = p2p('messages.js');

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
  const genesisBlock = await ask(probe, 'getdata', encodeGetData([{ type: MSG_BLOCK, hash: genesis }]), (m) => {
    if (m.command === 'block') {
      return { value: 'block' };
    }
    if (m.command === 'notfound' && decodeNotFoundBlocks(m.payload).some((h) => h.equals(genesis))) {
      return { value: 'notfound' };
    }
    return false;
  });
  knuth.getdataBlock = genesisBlock.answered ? genesisBlock.value : genesisBlock.what;
  check('kth serves a block to getdata (block mode downloads matching blocks)', genesisBlock.value === 'block', knuth.getdataBlock);
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

  // The explorer's filter store, synced from kth; its peer also downloads blocks in block mode.
  const peer = newPeer('kth filters and blocks');
  const sentBlock = counted(peer, 'block mode and store');
  const store = new FilterStore(path.join(OUT_DIR, 'filters'), peer, { network: 'regtest', pollInterval: 2000 });
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
                ms: Date.now() - t,
              }
        );
      }
    };
    await compare('block', '', blocks);
    await until('the range peer', () => rangePeer.isReady, 30_000);
    await compare('range', '', blocks);

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
    }
  } finally {
    rangePeer.stop();
    peer.stop();
    await store.stop().catch(() => undefined);
  }
}

main()
  .then(() => finish(0))
  .catch((e) => {
    check('the run completed', false, { error: e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : String(e) });
    finish(2);
  });
