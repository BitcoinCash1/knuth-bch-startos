#!/usr/bin/env python3
"""TEST ONLY. Rostrum, the Electrum server of Bitcoin Unlimited, run against
kth, with Rostrum run against BCHN as the reference.

A BCHN regtest node mines a chain with wallet transactions; kth (the package
image) syncs it over P2P. First, every node call Rostrum makes goes to both
nodes in the form Rostrum's own client (rust-jsonrpc 0.19) sends it: integer
ids, "jsonrpc":"2.0", "params":null for a call without arguments, and a JSON
array for a batch. Then two Rostrums index the chain, one on BCHN and one on
kth, each through its node's JSON-RPC and P2P ports. Rostrum on kth must start,
reach the tip and answer every Electrum method below as Rostrum on BCHN does.
Last, BCHN mines a block and both Rostrums must follow it.

Writes <out>/rostrum.json, <out>/rostrum.md, the logs of both Rostrums and of
kth, and <out>/rostrum-status.txt ("rostrum_e2e exit 0" when every check
passed). Always exits 0, so the logs are kept; the workflow reads the status.
Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-out")
ROSTRUM = os.environ.get("ROSTRUM_BIN") or "rostrum/target/release/rostrum"
WORK = Path("/tmp/rostrum-e2e")
BCHN_DIR = WORK / "bchn"
# Ports of their own, apart from scripts/knuth_rpc_e2e.py's, so the two never meet.
BCHN = ("127.0.0.1", 18643)
BCHN_P2P = 18644
KTH = ("127.0.0.1", 19632)
KTH_P2P = 18655
KTH_CONTAINER = "kth-rostrum"
NODES = {"kth": KTH, "bchn": BCHN}
P2P = {"kth": KTH_P2P, "bchn": BCHN_P2P}
ELECTRUM = {"kth": 60611, "bchn": 60601}
AUTH = base64.b64encode(b"e2e:e2e").decode()
RESULTS: dict = {"setup": {}, "rostrum": {}, "node_calls": [], "electrum": [], "follow": []}
PROCESSES: dict = {}


# ---- the node's JSON-RPC, as Rostrum's client sends it ----------------------

_next_id = 0


def request_object(method: str, params) -> dict:
    """rust-jsonrpc 0.19's Request, in its field order: params is null when
    Rostrum passes none."""
    global _next_id
    _next_id += 1
    return {"method": method, "params": params, "id": _next_id, "jsonrpc": "2.0"}


def post(node, payload, timeout: float = 60) -> dict:
    body = json.dumps(payload).encode()
    req = Request(f"http://{node[0]}:{node[1]}/", body,
                  {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read() or b""
    except (URLError, OSError) as error:
        return {"status": None, "json": None, "raw": f"{type(error).__name__}: {error}"}
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return {"status": status, "json": parsed, "raw": raw[:300].decode("utf-8", "replace")}


def call(node, method: str, params=None):
    """One request; (result, error). A reply that is not a response object is
    an error naming what came back."""
    reply = post(node, request_object(method, params))
    j = reply["json"]
    if isinstance(j, dict):
        return j.get("result"), j.get("error")
    return None, {"status": reply["status"], "raw": reply["raw"]}


def batch(node, method: str, params_list: list):
    """One batch, matched by id as rust-jsonrpc matches it: [(result, error)]
    in request order, or (None, why) when the reply is not a usable batch."""
    requests = [request_object(method, p) for p in params_list]
    reply = post(node, requests, timeout=120)
    j = reply["json"]
    if not isinstance(j, list):
        return None, {"status": reply["status"], "reply": j if j is not None else reply["raw"]}
    by_id = {}
    for item in j:
        if not isinstance(item, dict) or "id" not in item:
            return None, {"not a response object": item}
        if item["id"] in by_id:
            return None, {"duplicate id": item["id"]}
        by_id[item["id"]] = item
    out = []
    for r in requests:
        item = by_id.pop(r["id"], None)
        out.append((None, {"missing": r["id"]}) if item is None else (item.get("result"), item.get("error")))
    if by_id:
        return None, {"unknown ids": list(by_id)}
    return out, None


def cli(*args) -> str:
    return subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_DIR}", "-rpcuser=e2e",
                           "-rpcpassword=e2e", f"-rpcport={BCHN[1]}", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


# ---- nodes ------------------------------------------------------------------

def wait_answering(node, name: str, seconds: int = 180) -> None:
    for _ in range(seconds):
        result, error = call(node, "getblockcount", [])
        if error is None and result is not None:
            return
        time.sleep(1)
    raise RuntimeError(f"{name} never answered getblockcount")


def start_bchn() -> None:
    BCHN_DIR.mkdir(parents=True, exist_ok=True)
    # No -txindex: Rostrum passes the block hash for every confirmed
    # transaction, and kth keeps no transaction index either.
    subprocess.run(["bitcoind", "-regtest", f"-datadir={BCHN_DIR}", "-daemon", "-server",
                    "-rpcuser=e2e", "-rpcpassword=e2e", f"-rpcport={BCHN[1]}", f"-port={BCHN_P2P}",
                    "-listen=1", "-fallbackfee=0.0001"], check=True)
    wait_answering(BCHN, "BCHN")


def start_kth() -> None:
    subprocess.run(["docker", "rm", "-f", KTH_CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "--detach", "--name", KTH_CONTAINER, "--network", "host",
                    "--platform", "linux/amd64", "--entrypoint", "/usr/local/bin/kth", "knuth-e2e",
                    "--init_run", "--network", "regtest", "--node.display", "daemon",
                    "--db.directory", "/data/rostrum", "--db.db_mode", "full",
                    # Inbound P2P for Rostrum, which downloads blocks and hears of
                    # new blocks and transactions over it.
                    "--net.inbound_port", str(KTH_P2P), "--net.inbound_connections", "8",
                    "--net.outbound_connections", "1", "--net.peer", f"127.0.0.1:{BCHN_P2P}",
                    "--rpc.enabled", "true", "--rpc.bind", "127.0.0.1", "--rpc.port", str(KTH[1]),
                    "--rpc.user", "e2e", "--rpc.password", "e2e",
                    "--log.debug_file", "/data/kth-rostrum-debug.log",
                    "--log.error_file", "/data/kth-rostrum-error.log"], check=True)
    wait_answering(KTH, "kth")


def wait_height(node, height: int, seconds: int) -> bool:
    for _ in range(seconds):
        result, error = call(node, "getblockcount", [])
        if error is None and isinstance(result, int) and result >= height:
            return True
        time.sleep(1)
    return False


def wait_mempool(node, txids: list, seconds: int) -> bool:
    for _ in range(seconds):
        result, error = call(node, "getrawmempool", [False])
        if error is None and isinstance(result, list) and set(txids) <= set(result):
            return True
        time.sleep(1)
    return False


# ---- the chain --------------------------------------------------------------

def wallet_address() -> str:
    try:
        return cli("getnewaddress")
    except subprocess.CalledProcessError:
        cli("createwallet", "e2e")
        return cli("getnewaddress")


def scripthash(address: str) -> str:
    """The Electrum script hash: SHA-256 of the output script, reversed."""
    script = bytes.fromhex(json.loads(cli("getaddressinfo", address))["scriptPubKey"])
    return hashlib.sha256(script).digest()[::-1].hex()


def outputs_of(txid: str) -> list:
    raw = json.loads(cli("gettransaction", txid))["hex"]
    return json.loads(cli("decoderawtransaction", raw))["vout"]


def make_chain() -> dict:
    """BCHN only. W receives a confirmed payment it keeps and, later, a mempool
    one. S is confirmed and every output of it is spent by T, also confirmed."""
    miner = wallet_address()
    cli("generatetoaddress", "110", miner)
    watched, other, sink = cli("getnewaddress"), cli("getnewaddress"), cli("getnewaddress")
    paid = cli("sendtoaddress", watched, "2.0")
    spent = cli("sendtoaddress", other, "1.0")
    block = json.loads(cli("generatetoaddress", "1", miner))[0]
    # Keep W's coin out of the wallet's coin selection, so it stays unspent.
    kept = next(o["n"] for o in outputs_of(paid)
                if watched in o["scriptPubKey"].get("addresses", []) or
                o["scriptPubKey"].get("address") == watched)
    cli("lockunspent", "false", json.dumps([{"txid": paid, "vout": kept}]))
    outs = outputs_of(spent)
    total = sum(o["value"] for o in outs)
    raw = cli("createrawtransaction", json.dumps([{"txid": spent, "vout": o["n"]} for o in outs]),
              json.dumps({sink: round(total - 0.001, 8)}))
    spender = cli("sendrawtransaction", json.loads(cli("signrawtransactionwithwallet", raw))["hex"])
    spend_block = json.loads(cli("generatetoaddress", "1", miner))[0]
    cli("generatetoaddress", "2", miner)
    return {"miner": miner, "watched": watched, "paid": paid, "spent": spent, "spender": spender,
            "spent_block": block, "spent_height": json.loads(cli("getblock", block, "1"))["height"],
            "spend_block": spend_block, "height": int(cli("getblockcount"))}


def fresh_tx(to: str) -> str:
    """A signed, unsent payment to `to` from a wallet coin no other one uses."""
    utxo = next(u for u in json.loads(cli("listunspent", "1"))
                if u["amount"] >= 1 and u.get("spendable", True))
    cli("lockunspent", "false", json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]))
    raw = cli("createrawtransaction", json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]),
              json.dumps({to: round(utxo["amount"] - 0.001, 8)}))
    return json.loads(cli("signrawtransactionwithwallet", raw))["hex"]


def txid_of(raw_hex: str) -> str:
    return hashlib.sha256(hashlib.sha256(bytes.fromhex(raw_hex)).digest()).digest()[::-1].hex()


# ---- node calls -------------------------------------------------------------

def node_row(call_name: str, use: str, kth, bchn, ok: bool, note: str = "") -> None:
    RESULTS["node_calls"].append({"call": call_name, "rostrum": use, "kth": kth, "bchn": bchn,
                                  "verdict": "OK" if ok else "GAP", "note": note})


def answered(pair) -> bool:
    return pair[1] is None


def node_calls(s: dict) -> None:
    both = lambda method, params=None: {n: call(NODES[n], method, params) for n in NODES}

    r = both("ping")
    node_row("ping (params null)", "the RPC pool's connection check (manager.rs is_valid)",
             r["kth"], r["bchn"], answered(r["kth"]) and r["kth"][0] is None)

    r = both("getnetworkinfo")
    k = r["kth"][0] if answered(r["kth"]) else None
    fields_ok = isinstance(k, dict) and isinstance(k.get("version"), int) and \
        isinstance(k.get("subversion"), str) and isinstance(k.get("relayfee"), (int, float))
    same_fee = fields_ok and answered(r["bchn"]) and k["relayfee"] == r["bchn"][0]["relayfee"]
    node_row("getnetworkinfo (params null)", "startup; version, subversion, relayfee (blockchain.relayfee)",
             {"version": k.get("version"), "subversion": k.get("subversion"), "relayfee": k.get("relayfee")}
             if isinstance(k, dict) else r["kth"],
             {f: r["bchn"][0].get(f) for f in ("version", "subversion", "relayfee")} if answered(r["bchn"]) else r["bchn"],
             fields_ok and same_fee, "relayfee must equal BCHN's; version and subversion are the node's own")

    r = both("getblockchaininfo")
    wanted = {"chain": str, "blocks": int, "headers": int, "verificationprogress": (int, float),
              "bestblockhash": str, "pruned": bool, "initialblockdownload": bool}
    pick = lambda d: {f: d.get(f) for f in wanted} if isinstance(d, dict) else d
    k = r["kth"][0] if answered(r["kth"]) else None
    b = r["bchn"][0] if answered(r["bchn"]) else {}
    ok = isinstance(k, dict) and all(isinstance(k.get(f), t) and not (t is int and isinstance(k.get(f), bool))
                                     for f, t in wanted.items()) and \
        all(k.get(f) == b.get(f) for f in ("blocks", "bestblockhash", "pruned"))
    node_row("getblockchaininfo (params null)", "startup: every field of its BlockchainInfo; a pruned node is refused",
             pick(k) if k is not None else r["kth"], pick(b), ok)

    r = both("getmempoolinfo")
    k = r["kth"][0] if answered(r["kth"]) else None
    b = r["bchn"][0] if answered(r["bchn"]) else {}
    ok = isinstance(k, dict) and isinstance(k.get("mempoolminfee"), (int, float)) and \
        k.get("mempoolminfee") == b.get("mempoolminfee")
    node_row("getmempoolinfo (params null)", "blockchain.estimatefee: mempoolminfee, coins per kB",
             {f: k.get(f) for f in ("size", "mempoolminfee", "minrelaytxfee")} if isinstance(k, dict) else r["kth"],
             {f: b.get(f) for f in ("size", "mempoolminfee", "minrelaytxfee")}, ok)

    for method in ("getbestblockhash", "getblockcount"):
        r = both(method)
        node_row(f"{method} (params null)", "the tip it polls", r["kth"], r["bchn"],
                 answered(r["kth"]) and r["kth"] == r["bchn"])

    r = both("getblockhash", [0])
    node_row("getblockhash [0]", "genesis", r["kth"], r["bchn"], answered(r["kth"]) and r["kth"] == r["bchn"])
    genesis = r["bchn"][0]
    r = both("getblock", [genesis, 0])
    node_row("getblock [genesis, 0]", "genesis block", (r["kth"][0] or "")[:40] if answered(r["kth"]) else r["kth"],
             (r["bchn"][0] or "")[:40], answered(r["kth"]) and r["kth"] == r["bchn"])

    header_fields = ("hash", "height", "version", "previousblockhash", "merkleroot", "time", "bits", "nonce")
    hdr = lambda d: {f: d.get(f) for f in header_fields} if isinstance(d, dict) else d
    tip = call(BCHN, "getbestblockhash", [])[0]
    for label, params in (("getblockheader [tip]", [tip]), ("getblockheader [hash, true]", [s["spent_block"], True])):
        r = both("getblockheader", params)
        ok = answered(r["kth"]) and hdr(r["kth"][0]) == hdr(r["bchn"][0])
        node_row(label, "all headers: the tip's height; new headers by hash",
                 hdr(r["kth"][0]) if answered(r["kth"]) else r["kth"], hdr(r["bchn"][0]), ok)

    heights = list(range(0, s["height"] + 1))
    out = {n: batch(NODES[n], "getblockheader", [[h, False] for h in heights]) for n in NODES}
    k, b = out["kth"], out["bchn"]
    ok = k[0] is not None and b[0] is not None and k[0] == b[0] and all(e is None for _, e in k[0])
    summary = lambda x: x[1] if x[0] is None else {
        "items": len(x[0]), "errors": [e for _, e in x[0] if e is not None][:2],
        "first": (x[0][0][0] or "")[:24]}
    node_row(f"batch of {len(heights)} x getblockheader [height, false]",
             "first sync: every header by height (getblockheaders, chunks of 100000)", summary(k), summary(b), ok)

    unknown = "00" * 32
    members = [[s["mempool_txid"]], [unknown]]
    out = {n: batch(NODES[n], "getrawtransaction", members) for n in NODES}
    k, b = out["kth"], out["bchn"]
    ok = k[0] is not None and len(k[0]) == 2 and k[0][0] == b[0][0] and k[0][1][0] is None and \
        isinstance(k[0][1][1], dict) and k[0][1][1].get("code") == (b[0][1][1] or {}).get("code")
    brief = lambda x: x[1] if x[0] is None else [
        {"result": (res or "")[:16] if isinstance(res, str) else res, "error": err} for res, err in x[0]]
    node_row("batch of getrawtransaction [txid]: one in the mempool, one unknown",
             "mempool transactions (gettransactions_raw keeps member errors)", brief(k), brief(b), ok,
             "the unknown member's error code must be BCHN's")

    r = both("getblock", [s["spent_block"], 1])
    tx = lambda x: (x[0] or {}).get("tx") if answered(x) else x
    node_row("getblock [hash, 1]", "txids of a block (blockchain.transaction.get_merkle)",
             tx(r["kth"]), tx(r["bchn"]), answered(r["kth"]) and tx(r["kth"]) == tx(r["bchn"]))

    r = both("getblock", [s["spend_block"], 0])
    node_row("getblock [hash, 0]", "blocks over RPC when P2P does not deliver",
             (r["kth"][0] or "")[:40] if answered(r["kth"]) else r["kth"], (r["bchn"][0] or "")[:40],
             answered(r["kth"]) and r["kth"] == r["bchn"])

    r = both("getrawtransaction", [s["spent"], False, s["spent_block"]])
    node_row("getrawtransaction [txid, false, blockhash]", "confirmed transactions, with the block from its index",
             (r["kth"][0] or "")[:40] if answered(r["kth"]) else r["kth"], (r["bchn"][0] or "")[:40],
             answered(r["kth"]) and r["kth"] == r["bchn"])

    r = both("getrawtransaction", [s["mempool_txid"]])
    node_row("getrawtransaction [txid]", "a mempool transaction",
             (r["kth"][0] or "")[:40] if answered(r["kth"]) else r["kth"], (r["bchn"][0] or "")[:40],
             answered(r["kth"]) and r["kth"] == r["bchn"])

    r = both("getrawmempool", [False])
    node_row("getrawmempool [false]", "the mempool it tracks",
             sorted(r["kth"][0]) if answered(r["kth"]) else r["kth"], sorted(r["bchn"][0]),
             answered(r["kth"]) and sorted(r["kth"][0]) == sorted(r["bchn"][0]))


# ---- Rostrum ------------------------------------------------------------------

def start_rostrum(name: str) -> None:
    db = WORK / f"rostrum-{name}"
    shutil.rmtree(db, ignore_errors=True)
    db.mkdir(parents=True)
    log = open(OUT / f"rostrum-{name}.log", "w")
    args = [ROSTRUM, "--network=regtest", f"--daemon-dir={db}",
            f"--daemon-rpc-addr={NODES[name][0]}:{NODES[name][1]}",
            f"--daemon-p2p-addr=127.0.0.1:{P2P[name]}", "--auth=e2e:e2e",
            f"--db-dir={db / 'db'}", f"--electrum-rpc-addr=127.0.0.1:{ELECTRUM[name]}",
            "--no-websocket", "--no-websocket-ssl", "--no-metrics", "--no-announce",
            "--wait-duration-secs=1", "-vvv"]
    RESULTS["rostrum"][name] = {"args": args[1:]}
    PROCESSES[name] = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, RUST_BACKTRACE="1"))


class Electrum:
    """A line-delimited JSON-RPC client over TCP, as Electrum wallets use."""

    def __init__(self, port: int, timeout: float = 60):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buf = b""
        self.next_id = 0

    def call(self, method: str, *params):
        self.next_id += 1
        self.sock.sendall(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                                      "params": list(params)}).encode() + b"\n")
        while True:
            while b"\n" not in self.buf:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("Electrum connection closed")
                self.buf += chunk
            line, self.buf = self.buf.split(b"\n", 1)
            message = json.loads(line)
            if message.get("id") == self.next_id:
                return message.get("result"), message.get("error")
            # A notification: skipped.

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def log_tail(name: str, lines: int = 25) -> list:
    path = OUT / f"rostrum-{name}.log"
    if not path.exists():
        return []
    return path.read_text(errors="replace").splitlines()[-lines:]


def first_problem(name: str) -> str | None:
    """The first error, warning about the node or panic in a Rostrum's log."""
    path = OUT / f"rostrum-{name}.log"
    if not path.exists():
        return None
    for line in path.read_text(errors="replace").splitlines():
        lowered = line.lower()
        if " error " in lowered or "panicked" in lowered or "error:" in lowered or \
                ("warn" in lowered and ("full node" in lowered or "failed" in lowered)):
            return line[:400]
    return None


def wait_synced(name: str, height: int, seconds: int) -> bool:
    """The Electrum server opens after Rostrum's first sync; then its tip must
    reach `height`."""
    info = RESULTS["rostrum"][name]
    started = time.monotonic()
    client = None
    while time.monotonic() - started < seconds:
        process = PROCESSES[name]
        if process.poll() is not None:
            info["exited"] = process.returncode
            break
        if client is None:
            try:
                client = Electrum(ELECTRUM[name])
                client.call("server.version", "rostrum-e2e", "1.4")
                info["electrum_up_seconds"] = round(time.monotonic() - started)
            except (OSError, ValueError):
                client = None
                time.sleep(1)
                continue
        try:
            result, error = client.call("blockchain.headers.subscribe")
        except (OSError, ValueError) as error_:
            info["electrum_error"] = f"{type(error_).__name__}: {error_}"
            client.close()
            client = None
            continue
        if error is None and isinstance(result, dict) and result.get("height", -1) >= height:
            info["synced_seconds"] = round(time.monotonic() - started)
            info["height"] = result["height"]
            client.close()
            return True
        time.sleep(1)
    info["synced"] = False
    info["first_problem"] = first_problem(name)
    info["log_tail"] = log_tail(name)
    if client is not None:
        client.close()
    return False


def electrum_row(label: str, k, b, ok: bool, note: str = "") -> None:
    RESULTS["electrum"].append({"method": label, "kth": k, "bchn": b,
                                "verdict": "OK" if ok else "DIFFERS", "note": note})


def electrum_checks(clients: dict, s: dict) -> None:
    watched = scripthash(s["watched"])
    checks = [
        ("server.version", ["rostrum-e2e", "1.4"]),
        ("blockchain.headers.subscribe", []),
        ("blockchain.block.header", [s["spent_height"]]),
        ("blockchain.block.header", [0]),
        ("blockchain.scripthash.get_history", [watched]),
        ("blockchain.scripthash.get_balance", [watched]),
        ("blockchain.scripthash.listunspent", [watched]),
        ("blockchain.scripthash.get_mempool", [watched]),
        ("blockchain.transaction.get", [s["spent"]]),
        ("blockchain.transaction.get", [s["spent"], True]),
        ("blockchain.transaction.get", [s["mempool_txid"]]),
        ("blockchain.transaction.get_merkle", [s["spent"], s["spent_height"]]),
        ("blockchain.transaction.get_confirmed_blockhash", [s["spent"]]),
        ("blockchain.relayfee", []),
        ("blockchain.estimatefee", [2]),
    ]
    notes = {("blockchain.transaction.get", 1): "confirmed; every output of it is spent",
             ("blockchain.transaction.get", 2): "confirmed, all outputs spent, verbose",
             ("blockchain.transaction.get", 3): "in the mempool"}
    seen: dict = {}
    for method, params in checks:
        seen[method] = seen.get(method, 0) + 1
        answers = {}
        for name, client in clients.items():
            try:
                answers[name] = client.call(method, *params)
            except (OSError, ValueError) as error:
                answers[name] = (None, {"transport": f"{type(error).__name__}: {error}"})
        k, b = answers.get("kth"), answers.get("bchn")
        ok = k is not None and b is not None and k[1] is None and b[1] is None and k[0] == b[0]
        electrum_row(f"{method} {json.dumps(params)[:90]}", k, b, ok, notes.get((method, seen[method]), ""))

    # Broadcast: each Rostrum sends its own transaction to its node; each must
    # answer with that transaction's id.
    raws = {name: fresh_tx(s["broadcast_to"]) for name in clients}
    results = {}
    for name, client in clients.items():
        try:
            results[name] = client.call("blockchain.transaction.broadcast", raws[name])
        except (OSError, ValueError) as error:
            results[name] = (None, {"transport": f"{type(error).__name__}: {error}"})
    k_ok = results.get("kth", (None, 1))[0] == txid_of(raws["kth"]) if "kth" in raws else False
    b_ok = results.get("bchn", (None, 1))[0] == txid_of(raws["bchn"]) if "bchn" in raws else False
    electrum_row("blockchain.transaction.broadcast [raw]", results.get("kth"), results.get("bchn"),
                 k_ok and b_ok, "each answer must be the txid of the transaction it sent")
    s["broadcast_txids"] = [txid_of(r) for r in raws.values()]


def follow_checks(clients: dict, s: dict) -> None:
    """Both broadcasts reach both nodes and both Rostrums; then a new block is
    followed by both Rostrums."""
    target = scripthash(s["broadcast_to"])
    deadline = time.monotonic() + 90
    mempools = {}
    while time.monotonic() < deadline:
        mempools = {name: c.call("blockchain.scripthash.get_mempool", target) for name, c in clients.items()}
        if all(e is None and isinstance(r, list) and len(r) == len(s["broadcast_txids"])
               for r, e in mempools.values()):
            break
        time.sleep(2)
    k, b = mempools.get("kth"), mempools.get("bchn")
    RESULTS["follow"].append({"check": "both broadcasts in both Rostrums' get_mempool",
                              "kth": k, "bchn": b,
                              "verdict": "OK" if k is not None and k == b and k[1] is None and
                              len(k[0] or []) == len(s["broadcast_txids"]) else "DIFFERS"})

    block = json.loads(cli("generatetoaddress", "1", s["miner"]))[0]
    height = int(cli("getblockcount"))
    synced = {name: False for name in clients}
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and not all(synced.values()):
        for name, c in clients.items():
            r, e = c.call("blockchain.headers.subscribe")
            synced[name] = e is None and isinstance(r, dict) and r.get("height") == height
        time.sleep(2)
    RESULTS["follow"].append({"check": f"both Rostrums reach the new tip {height} ({block[:16]}...)",
                              "kth": synced.get("kth"), "bchn": synced.get("bchn"),
                              "verdict": "OK" if all(synced.values()) else "DIFFERS"})
    time.sleep(3)
    watched = scripthash(s["watched"])
    for method in ("blockchain.scripthash.get_history", "blockchain.scripthash.get_balance",
                   "blockchain.scripthash.get_mempool", "blockchain.headers.subscribe"):
        params = [] if method == "blockchain.headers.subscribe" else [watched]
        a = {name: c.call(method, *params) for name, c in clients.items()}
        k, b = a.get("kth"), a.get("bchn")
        RESULTS["follow"].append({"check": f"after the block: {method}", "kth": k, "bchn": b,
                                  "verdict": "OK" if k is not None and k == b and k[1] is None else "DIFFERS"})


# ---- report -------------------------------------------------------------------

def cell(value, limit: int = 200) -> str:
    return json.dumps(value, default=str)[:limit].replace("|", "\\|")


def report() -> str:
    lines = ["# Rostrum on kth vs Rostrum on BCHN 29.2.0, regtest", ""]
    for k, v in RESULTS["setup"].items():
        lines.append(f"- {k}: `{cell(v, 300)}`")
    lines += ["", "## Rostrum", ""]
    for name, info in RESULTS["rostrum"].items():
        shown = {k: v for k, v in info.items() if k not in ("args", "log_tail")}
        lines.append(f"- {name}: `{cell(shown, 600)}`")
        if info.get("log_tail"):
            lines += ["", f"  Last lines of Rostrum on {name}:", "", "```"] + info["log_tail"] + ["```", ""]
    lines += ["", "## Node calls, as Rostrum sends them", "",
              "| Call | Rostrum uses it for | Verdict | kth | BCHN |", "|---|---|---|---|---|"]
    for r in RESULTS["node_calls"]:
        lines.append(f"| `{r['call']}` | {r['rostrum']} | **{r['verdict']}** | {cell(r['kth'])} | {cell(r['bchn'])} |")
    lines += ["", "## Electrum methods: Rostrum on kth vs Rostrum on BCHN", "",
              "| Method | Note | Verdict | Rostrum on kth | Rostrum on BCHN |", "|---|---|---|---|---|"]
    for r in RESULTS["electrum"]:
        lines.append(f"| `{r['method']}` | {r['note']} | **{r['verdict']}** | {cell(r['kth'])} | {cell(r['bchn'])} |")
    lines += ["", "## Following the node", "", "| Check | Verdict | Rostrum on kth | Rostrum on BCHN |",
              "|---|---|---|---|"]
    for r in RESULTS["follow"]:
        lines.append(f"| {r['check']} | **{r['verdict']}** | {cell(r['kth'])} | {cell(r['bchn'])} |")
    failed = [r["call"] for r in RESULTS["node_calls"] if r["verdict"] != "OK"]
    failed += [r["method"] for r in RESULTS["electrum"] if r["verdict"] != "OK"]
    failed += [r["check"] for r in RESULTS["follow"] if r["verdict"] != "OK"]
    lines += ["", f"## {len(failed)} failed", ""] + [f"- {f}" for f in failed]
    return "\n".join(lines)


def passed() -> bool:
    rostrum = RESULTS["rostrum"]
    return bool(RESULTS["electrum"]) and bool(RESULTS["follow"]) and \
        all(rostrum.get(n, {}).get("height") is not None for n in NODES) and \
        all(r["verdict"] == "OK" for key in ("node_calls", "electrum", "follow") for r in RESULTS[key])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    clients: dict = {}
    try:
        version = subprocess.run([ROSTRUM, "--version"], capture_output=True, text=True)
        RESULTS["setup"]["rostrum_version"] = (version.stdout + version.stderr).strip()
        start_bchn()
        s = make_chain()
        started = time.monotonic()
        start_kth()
        RESULTS["setup"]["kth_synced"] = wait_height(KTH, s["height"], 900)
        RESULTS["setup"]["kth_sync_seconds"] = round(time.monotonic() - started)
        s["broadcast_to"] = cli("getnewaddress")
        s["mempool_txid"] = cli("sendtoaddress", s["watched"], "0.5")
        RESULTS["setup"]["kth_has_mempool_tx"] = wait_mempool(KTH, [s["mempool_txid"]], 60)
        RESULTS["setup"]["chain"] = {k: v for k, v in s.items() if k != "miner"}
        try:
            node_calls(s)
        except Exception as error:  # the Rostrum part must still run
            RESULTS["setup"]["node_calls_crashed"] = f"{type(error).__name__}: {error}"
            traceback.print_exc()
        for name in ("bchn", "kth"):
            start_rostrum(name)
        for name in ("bchn", "kth"):
            if wait_synced(name, s["height"], 300):
                # A connection of its own, opened with server.version as a
                # wallet opens one.
                clients[name] = Electrum(ELECTRUM[name])
        if "bchn" not in clients:
            RESULTS["setup"]["reference_failed"] = "Rostrum on BCHN did not sync; nothing to compare with"
        if clients:
            electrum_checks(clients, s)
            follow_checks(clients, s)
    except Exception as error:
        RESULTS["setup"]["crashed"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    finally:
        for client in clients.values():
            client.close()
        for name, process in PROCESSES.items():
            if process.poll() is None:
                process.send_signal(2)
                try:
                    process.wait(30)
                except subprocess.TimeoutExpired:
                    process.kill()
            RESULTS["rostrum"].setdefault(name, {})["exit"] = process.returncode
        subprocess.run(["docker", "stop", "-t", "15", KTH_CONTAINER], capture_output=True)
        subprocess.run(["docker", "cp", f"{KTH_CONTAINER}:/data/kth-rostrum-debug.log",
                        str(OUT / "kth-rostrum-debug.log")], capture_output=True)
        subprocess.run(["docker", "rm", "-f", KTH_CONTAINER], capture_output=True)
        subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_DIR}", "-rpcuser=e2e",
                        "-rpcpassword=e2e", f"-rpcport={BCHN[1]}", "stop"], capture_output=True)
        bchn_log = BCHN_DIR / "regtest" / "debug.log"
        if bchn_log.exists():
            (OUT / "rostrum-bchn-debug.log").write_text(bchn_log.read_text(errors="replace")[-400000:])
        ok = passed()
        (OUT / "rostrum.json").write_text(json.dumps(RESULTS, indent=1, default=str))
        text = report()
        (OUT / "rostrum.md").write_text(text)
        (OUT / "rostrum-status.txt").write_text(f"rostrum_e2e exit {0 if ok else 1}\n")
        print(text)


if __name__ == "__main__":
    main()
