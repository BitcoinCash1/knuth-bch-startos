#!/usr/bin/env python3
"""TEST ONLY. End-to-end check of kth's own JSON-RPC, with no proxy, against
the node calls Fulcrum, BCH Explorer, ASICSeer and EloPool make.

A BCHN regtest node mines a chain with wallet transactions; kth (built from the
package Dockerfile) syncs it over P2P. Every call then goes to both nodes, so a
kth failure is shown next to what BCHN answers for the very same request. The
transport checks send each client's exact request bytes and read the reply the
way that client's own code does.

Writes <out>/results.json and <out>/results.md, and prints the table.
Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import select
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-out")
BCHN_DIR = Path("/tmp/bchn-regtest")
BCHN = ("127.0.0.1", 18443)
KTH = ("127.0.0.1", 19332)
NODES = {"kth": KTH, "bchn": BCHN}
AUTH = base64.b64encode(b"e2e:e2e").decode()
RESULTS: dict = {"setup": {}, "methods": [], "transport": [], "mining": []}


# ---- plain JSON-RPC ---------------------------------------------------------

def post(node, body: bytes, timeout: float = 60) -> dict:
    request = Request(f"http://{node[0]}:{node[1]}/", body,
                      {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read() or b""
    except (URLError, OSError) as error:
        return {"status": None, "transport": f"{type(error).__name__}: {error}", "raw": b"", "json": None}
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return {"status": status, "raw": raw, "json": parsed,
            "ms": round((time.monotonic() - started) * 1000)}


_next_id = 0


def call(node, method: str, *params):
    """The body every app here sends: no "jsonrpc" key, integer id."""
    global _next_id
    _next_id += 1
    reply = post(node, json.dumps({"id": _next_id, "method": method, "params": list(params)}).encode())
    j = reply["json"]
    if isinstance(j, dict):
        return j.get("result"), j.get("error")
    return None, {"transport": reply.get("transport"), "status": reply["status"],
                  "raw": reply["raw"][:200].decode("utf-8", "replace")}


def cli(*args) -> str:
    return subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_DIR}", "-rpcuser=e2e",
                           "-rpcpassword=e2e", "-rpcport=18443", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


# ---- nodes ------------------------------------------------------------------

def wait_for(node, name: str, seconds: int = 180) -> None:
    for _ in range(seconds):
        result, error = call(node, "getblockchaininfo")
        if error is None and result:
            return
        time.sleep(1)
    raise SystemExit(f"{name} never answered getblockchaininfo")


def start_bchn() -> None:
    BCHN_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bitcoind", "-regtest", f"-datadir={BCHN_DIR}", "-daemon", "-server",
                    "-rpcuser=e2e", "-rpcpassword=e2e", "-rpcport=18443", "-port=18444",
                    "-listen=1", "-txindex=1", "-fallbackfee=0.0001",
                    "-zmqpubhashblock=tcp://127.0.0.1:28332"], check=True)
    wait_for(BCHN, "BCHN")


def start_kth() -> None:
    subprocess.run(["docker", "run", "--detach", "--name", "kth", "--network", "host",
                    "--platform", "linux/amd64", "--entrypoint", "/usr/local/bin/kth", "knuth-e2e",
                    "--init_run", "--network", "regtest", "--node.display", "daemon",
                    "--db.directory", "/data/e2e", "--db.db_mode", "full",
                    "--net.inbound_port", "18555", "--net.inbound_connections", "0",
                    "--net.outbound_connections", "1", "--net.peer", "127.0.0.1:18444",
                    "--rpc.enabled", "true", "--rpc.bind", "127.0.0.1", "--rpc.port", "19332",
                    "--rpc.user", "e2e", "--rpc.password", "e2e"], check=True)
    wait_for(KTH, "kth")


def kth_log(lines: int = 40) -> str:
    out = subprocess.run(["docker", "logs", "--tail", str(lines), "kth"], capture_output=True, text=True)
    return out.stdout + out.stderr


def wait_kth_height(height: int, seconds: int) -> bool:
    for _ in range(seconds):
        info, error = call(KTH, "getblockchaininfo")
        if error is None and info and info.get("blocks", -1) >= height:
            return True
        time.sleep(1)
    return False


# ---- block building (from the knuth-mining-probe branch) --------------------

def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def push(data: bytes) -> bytes:
    return bytes([len(data)]) + data


def script_number(n: int) -> bytes:
    out = bytearray()
    while n:
        out.append(n & 0xFF)
        n >>= 8
    if out and out[-1] & 0x80:
        out.append(0)
    return bytes(out) or b"\x00"


def coinbase(height: int, value: int, tag: bytes) -> bytes:
    script_sig = push(script_number(height)) + push(tag.ljust(40, b"."))
    out_script = b"\x51"  # OP_TRUE
    return (struct.pack("<i", 2) + b"\x01" + b"\x00" * 32 + b"\xff\xff\xff\xff"
            + bytes([len(script_sig)]) + script_sig + b"\xff\xff\xff\xff"
            + b"\x01" + struct.pack("<q", value) + bytes([len(out_script)]) + out_script
            + struct.pack("<I", 0))


def solve_coinbase_only(template: dict, tag: bytes) -> tuple[str, str, str]:
    """Coinbase-only block on a template: (block hex, block hash, coinbase hex).
    The coinbase claims the bare regtest subsidy: the template's coinbasevalue
    may include fees of transactions this block leaves out."""
    subsidy = (50 * 100_000_000) >> (template["height"] // 150)
    base = coinbase(template["height"], subsidy, tag)
    root = sha256d(base)
    prev = bytes.fromhex(template["previousblockhash"])[::-1]
    bits = int(template["bits"], 16)
    target = (bits & 0xFFFFFF) << (8 * ((bits >> 24) - 3))
    when = max(template["curtime"], template.get("mintime", 0))
    for nonce in range(1 << 32):
        header = (struct.pack("<I", template["version"]) + prev + root
                  + struct.pack("<III", when, bits, nonce))
        if int.from_bytes(sha256d(header)[::-1], "big") <= target:
            break
    return (header + b"\x01" + base).hex(), sha256d(header)[::-1].hex(), base.hex()


# ---- chain setup ------------------------------------------------------------

def setup() -> dict:
    start_bchn()
    cli("createwallet", "e2e")
    addr = cli("getnewaddress")
    cli("generatetoaddress", "110", addr)
    others = [cli("getnewaddress") for _ in range(3)]
    confirmed = [cli("sendtoaddress", a, "1.5") for a in others]
    tx_block = json.loads(cli("generatetoaddress", "1", addr))[0]
    cli("generatetoaddress", "2", addr)
    height = int(cli("getblockcount"))

    start_kth()
    synced = wait_kth_height(height, 900)
    RESULTS["setup"]["kth_synced_bchn_chain"] = synced
    RESULTS["setup"]["bchn_height"] = height
    if not synced:
        RESULTS["setup"]["kth_log"] = kth_log(60)

    # Transactions for the mempool calls: two through BCHN's own relay, then
    # time for kth to hear about them.
    pending = [cli("sendtoaddress", a, "0.25") for a in others[:2]]
    time.sleep(15)

    def fresh_tx() -> str:
        utxo = next(u for u in json.loads(cli("listunspent", "1"))
                    if u["amount"] >= 1 and u["txid"] not in confirmed)
        cli("lockunspent", "false", json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]))
        raw = cli("createrawtransaction",
                  json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]),
                  json.dumps({cli("getnewaddress"): round(utxo["amount"] - 0.001, 8)}))
        return json.loads(cli("signrawtransactionwithwallet", raw))["hex"]

    block = json.loads(cli("getblock", tx_block, "1"))
    unspent_n = next(n for n in range(4)
                     if cli("gettxout", confirmed[0], str(n)) not in ("", "null"))
    return {
        "addr": addr, "height": height, "tip": cli("getbestblockhash"),
        "tx_block": tx_block, "tx_block_height": block["height"],
        "coinbase_txid": block["tx"][0], "confirmed_txid": confirmed[0], "unspent_n": unspent_n,
        "mempool_txids": pending, "fresh_tx_send": fresh_tx(), "fresh_tx_test": fresh_tx(),
    }


# ---- method matrix ------------------------------------------------------------

def method_check(method, params, apps, *, needed=(), same=None, tolerated_codes=(), label=None,
                 bchn_params=None):
    """Call both nodes. Verdict: ok, GAP, or n/a (BCHN fails the same call).
    bchn_params: the form the app sends to BCHN when it differs (Fulcrum picks
    the estimatefee form by node name)."""
    kr, ke = call(KTH, method, *params)
    br, be = call(BCHN, method, *(params if bchn_params is None else bchn_params))
    row = {"call": label or f"{method} {json.dumps(params)[:70]}", "apps": apps,
           "kth": brief(kr, ke), "bchn": brief(br, be)}
    if be is not None:
        row["verdict"] = "n/a: BCHN fails this call too"
    elif ke is not None:
        code = ke.get("code") if isinstance(ke, dict) else None
        tolerated = code in tolerated_codes
        row["verdict"] = "GAP (kth errors; the app tolerates this code)" if tolerated else "GAP"
    else:
        problems = []
        if needed and isinstance(kr, dict):
            missing = [f for f in needed if f not in kr]
            if missing:
                problems.append(f"missing fields the apps read: {missing}")
        if isinstance(kr, dict) and isinstance(br, dict):
            extra = sorted(set(br) - set(kr) - set(needed))
            if extra:
                row["fields_only_bchn_has"] = extra
        if same is not None:
            note = same(kr, br)
            if note:
                problems.append(note)
        row["verdict"] = "GAP (" + "; ".join(problems) + ")" if problems else "ok"
    RESULTS["methods"].append(row)
    return kr, br


def brief(result, error, limit: int = 160):
    if error is not None:
        return {"error": error}
    text = json.dumps(result)
    return {"result": text if len(text) <= limit else text[:limit] + f"... ({len(text)} chars)"}


def eq(kr, br):
    return None if kr == br else "differs from BCHN"


def methods(s: dict) -> None:
    tip, blk, txid = s["tip"], s["tx_block"], s["confirmed_txid"]

    def chaininfo_same(k, b):
        notes = []
        for f in ("blocks", "bestblockhash", "chain"):
            if k.get(f) != b.get(f):
                notes.append(f"{f} {k.get(f)!r} vs BCHN {b.get(f)!r}")
        if k.get("initialblockdownload") is not False:
            notes.append(f"initialblockdownload is {k.get('initialblockdownload')!r} at the tip "
                         "(Fulcrum never opens its port while it is true)")
        if k.get("headers") != k.get("blocks"):
            notes.append("headers != blocks (the Explorer indexer waits for equality)")
        return "; ".join(notes) or None

    method_check("help", ["help"], "Fulcrum! (auth check every connect)")
    method_check("uptime", [], "Fulcrum probe")
    method_check("getnetworkinfo", [], "Fulcrum!, Explorer, pool stats",
                 needed=("subversion", "version", "relayfee", "warnings"))
    method_check("getblockchaininfo", [], "Fulcrum!, Explorer!, ASICSeer!, EloPool",
                 needed=("blocks", "chain", "bestblockhash", "headers", "initialblockdownload",
                         "difficulty", "mediantime", "verificationprogress", "chainwork",
                         "size_on_disk", "pruned", "warnings"), same=chaininfo_same)
    method_check("getblockcount", [], "Explorer!, pool health", same=eq)
    method_check("getbestblockhash", [], "Explorer", same=eq)
    method_check("getblockhash", [0], "Fulcrum!", same=eq)
    method_check("getblockhash", [s["tx_block_height"]], "Fulcrum!, Explorer!, pools", same=eq)
    method_check("getblock", [blk, False], "Fulcrum! (raw block)", same=eq)
    method_check("getblock", [blk, 0], "Explorer route", same=eq)
    method_check("getblock", [blk], "Explorer!",
                 needed=("hash", "height", "version", "time", "bits", "nonce", "difficulty",
                         "merkleroot", "nTx", "size", "previousblockhash", "mediantime",
                         "confirmations"),
                 same=lambda k, b: None if k.get("tx") == b.get("tx") else "tx list differs")
    method_check("getblock", [blk, 1], "Explorer indexer",
                 same=lambda k, b: None if k.get("tx") == b.get("tx") else "tx list differs")

    def verbose2_same(k, b):
        kt, bt = k.get("tx") or [], b.get("tx") or []
        if [t.get("txid") for t in kt] != [t.get("txid") for t in bt]:
            return "tx list differs"
        if any("fee" not in t for t in kt[1:]):
            return "tx[].fee missing (Explorer reads it)"
        return None

    method_check("getblock", [blk, 2], "Explorer!", same=verbose2_same)
    method_check("getblock", [blk, 2, True], "Explorer reorg/route", same=verbose2_same)
    method_check("getblockheader", [blk, False], "Explorer!", same=eq)
    method_check("getblockheader", [blk, True], "Fulcrum client, package health",
                 needed=("confirmations", "height", "time"))
    method_check("getblockstats", [blk], "Explorer! (unless -32601)", tolerated_codes=(-32601,))
    method_check("getrawtransaction", [txid, False], "Fulcrum!", same=eq)
    method_check("getrawtransaction", [s["coinbase_txid"], False], "Fulcrum txindex check", same=eq)

    def verbose_tx_same(k, b):
        if k.get("hex") != b.get("hex"):
            return "hex differs"
        spk = ((k.get("vout") or [{}])[0]).get("scriptPubKey") or {}
        missing = [f for f in ("hex", "asm", "type", "addresses") if f not in spk]
        return f"vout[0].scriptPubKey lacks {missing} (Explorer reads them)" if missing else None

    method_check("getrawtransaction", [txid, True], "Explorer!, Fulcrum client",
                 needed=("txid", "vin", "vout", "confirmations", "blockhash", "blocktime"),
                 same=verbose_tx_same)
    method_check("getrawtransaction", [s["mempool_txids"][0], False], "Fulcrum! (mempool tx)", same=eq)

    def mempool_same(k, b):
        missing = sorted(set(b) - set(k)) if isinstance(b, list) else None
        return f"kth mempool lacks {len(missing)} of BCHN's {len(b)} txs" if missing else None

    method_check("getrawmempool", [False], "Fulcrum!", same=mempool_same)
    method_check("getrawmempool", [], "Explorer!", same=mempool_same)
    method_check("getrawmempool", [True], "Explorer")
    method_check("getmempoolentry", [s["mempool_txids"][0]], "Explorer (tolerated)")
    method_check("getmempoolinfo", [], "Fulcrum client, Explorer, pool stats")
    method_check("getzmqnotifications", [], "Fulcrum (optional, else polls)")
    method_check("getdsprooflist", [0], "Fulcrum (optional)")
    method_check("estimatefee", [6], "Fulcrum client (sends [n] to non-BCHN nodes)", bchn_params=[],
                 label="estimatefee [6] to kth, [] to BCHN")
    method_check("validateaddress", [s["addr"]], "ASICSeer!, Explorer route, Fulcrum admin",
                 needed=("isvalid", "address", "scriptPubKey"),
                 same=lambda k, b: None if k.get("isvalid") is True else "isvalid is not true")
    method_check("gettxout", [txid, s["unspent_n"]], "Explorer route", same=eq)
    method_check("gettxout", [txid, s["unspent_n"], False], "Explorer route", same=eq)
    method_check("testmempoolaccept", [[s["fresh_tx_test"]]], "Explorer route")
    method_check("getindexinfo", [], "Explorer indexer! (unless -32601)", tolerated_codes=(-32601,))
    method_check("getchaintips", [], "Explorer (tolerated)")
    method_check("getnetworkhashps", [1, s["tx_block_height"]], "Explorer indexer!")
    method_check("getnetworkhashps", [1, 1], "Explorer indexer!")
    method_check("getnetworkhashps", [1008], "Explorer route")
    method_check("getdifficulty", [], "Explorer (tolerated)")
    method_check("getmininginfo", [], "pool stats")
    gbt_fields = ("previousblockhash", "target", "version", "curtime", "bits", "height",
                  "coinbasevalue", "transactions")
    method_check("getblocktemplate",
                 [{"capabilities": ["coinbasetxn", "workid", "coinbase/append"], "rules": []}],
                 "ASICSeer!", needed=gbt_fields, label="getblocktemplate (ASICSeer request)")
    method_check("getblocktemplate",
                 [{"capabilities": ["coinbasetxn", "workid", "coinbase/append"]}],
                 "EloPool!", needed=gbt_fields, label="getblocktemplate (EloPool request)")
    method_check("preciousblock", [tip], "pools (result ignored)")


# ---- client transports ----------------------------------------------------------

class LineReader:
    """Port of ckpool's read_socket_line: returns (len, line) with the '\\n'
    stripped, len 0 on timeout without a whole line, -1 on close/error."""

    def __init__(self, sock: socket.socket):
        self.sock, self.buf = sock, b""

    def read_line(self, timeout: float) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return 0, self.buf
            ready, _, _ = select.select([self.sock], [], [], left)
            if not ready:
                return 0, self.buf
            chunk = self.sock.recv(65536)
            if not chunk:
                return -1, self.buf
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return len(line), line

    def read_exact(self, n: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while len(self.buf) < n and time.monotonic() < deadline:
            ready, _, _ = select.select([self.sock], [], [], deadline - time.monotonic())
            if not ready:
                break
            chunk = self.sock.recv(65536)
            if not chunk:
                break
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


def ckpool_request(node, body: bytes) -> bytes:
    return (f"POST / HTTP/1.1\nAuthorization: Basic {AUTH}\nHost: {node[0]}:{node[1]}\n"
            f"Content-type: application/json\nContent-Length: {len(body)}\n\n").encode() + body


def elopool_exchange(node, body: bytes, timeout: float = 15) -> tuple[bool, str]:
    """EloPool's json_rpc_call read path (ckpool.c): status line must start
    'HTTP/1.1 200 OK', then lines are read until one starts with '{'."""
    with socket.create_connection(node, timeout=timeout) as s:
        s.sendall(ckpool_request(node, body))
        r = LineReader(s)
        n, line = r.read_line(timeout)
        if n < 1:
            return False, f"no status line (read_socket_line returned {n})"
        if not line[:15].lower() == b"http/1.1 200 ok":
            return False, f"status line not ok: {line[:60]!r}"
        while True:
            n, line = r.read_line(timeout)
            if n < 1:
                return False, (f"no JSON line: read_socket_line returned {n} "
                               f"({'timeout' if n == 0 else 'closed'}) with {len(line)} unread bytes "
                               f"{line[:60]!r}")
            if line.startswith(b"{"):
                break
        try:
            json.loads(line)
        except ValueError as e:
            return False, f"JSON line does not parse: {e}"
        return True, "body read as one JSON line"


def asicseer_exchange(node, body: bytes, timeout: float = 15) -> tuple[bool, str]:
    """ASICSeer's json_rpc_call read path: status 'HTTP/1.1 200 OK', a
    'Content-Length: ' line, then the very next line must have length 1 (the
    '\\r' of a CRLF blank line), then exactly Content-Length bytes."""
    with socket.create_connection(node, timeout=timeout) as s:
        s.sendall(ckpool_request(node, body))
        r = LineReader(s)
        n, line = r.read_line(timeout)
        if n < 1 or line[:15].lower() != b"http/1.1 200 ok":
            return False, f"status line not ok: {line[:60]!r} (len {n})"
        length = -1
        while length < 0:
            n, line = r.read_line(timeout)
            if n < 1:
                return False, f"no Content-Length line (read_socket_line returned {n})"
            if line[:16].lower() == b"content-length: ":
                length = int(line[16:].strip())
                n, blank = r.read_line(timeout)
                if n != 1:
                    return False, (f"line after Content-Length is {blank!r} (len {n}), "
                                   "ASICSeer needs the blank line right after it")
        data = r.read_exact(length, timeout)
        try:
            json.loads(data)
        except ValueError as e:
            return False, f"body does not parse: {e}"
        return True, "body read by Content-Length"


def http_responses(data: bytes) -> list[dict]:
    """Split a byte stream into HTTP/1.1 responses framed by Content-Length."""
    out = []
    while data:
        head, sep, rest = data.partition(b"\r\n\r\n")
        if not sep:
            break
        lines = head.split(b"\r\n")
        headers = {}
        for h in lines[1:]:
            k, _, v = h.partition(b":")
            headers[k.strip().lower().decode()] = v.strip().decode()
        length = int(headers.get("content-length", "-1"))
        if length < 0 or len(rest) < length:
            out.append({"status_line": lines[0].decode(), "headers": headers, "body": rest, "short": True})
            break
        out.append({"status_line": lines[0].decode(), "headers": headers, "body": rest[:length]})
        data = rest[length:]
    return out


def fulcrum_exchange(node, timeout: float = 15) -> tuple[bool, str]:
    """Fulcrum's HttpConnection: CRLF headers, Content-Type application/json-rpc,
    body with a CRLF suffix counted in Content-Length, three requests pipelined
    on one keep-alive connection. Each reply must be HTTP/1.1, carry
    Content-Type and Content-Length, and echo the integer id with a result key
    (or an error with code and message)."""
    reqs = [("help", ["help"]), ("getblockchaininfo", []), ("getnetworkinfo", [])]
    payload = b""
    for i, (m, p) in enumerate(reqs, 1):
        body = json.dumps({"id": i, "method": m, "params": p}).encode() + b"\r\n"
        payload += (f"POST / HTTP/1.1\r\nHost: {node[0]}:{node[1]}\r\n"
                    f"Content-Type: application/json-rpc\r\nAuthorization: Basic {AUTH}\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n").encode() + body
    with socket.create_connection(node, timeout=timeout) as s:
        s.sendall(payload)
        data, deadline = b"", time.monotonic() + timeout
        while len(http_responses(data)) < len(reqs) and time.monotonic() < deadline:
            ready, _, _ = select.select([s], [], [], deadline - time.monotonic())
            if not ready:
                break
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    replies = http_responses(data)
    problems = []
    if len(replies) < len(reqs):
        problems.append(f"{len(replies)} of {len(reqs)} pipelined replies came back")
    for i, rep in enumerate(replies, 1):
        if not rep["status_line"].startswith("HTTP/1.1"):
            problems.append(f"reply {i} status line {rep['status_line']!r}")
        for h in ("content-type", "content-length"):
            if h not in rep["headers"]:
                problems.append(f"reply {i} has no {h} header")
        try:
            j = json.loads(rep["body"])
        except ValueError:
            problems.append(f"reply {i} body is not JSON")
            continue
        if not isinstance(j, dict) or j.get("id") != i or not isinstance(j.get("id"), int):
            problems.append(f"reply {i} id is {j.get('id') if isinstance(j, dict) else j!r}")
        elif j.get("error") is None and "result" not in j:
            problems.append(f"reply {i} has no result key")
        elif j.get("error") is not None and not ({"code", "message"} <= set(j["error"])):
            problems.append(f"reply {i} error lacks code/message")
    return (not problems), ("; ".join(problems) or "3 pipelined replies framed as Fulcrum expects")


def explorer_exchange(node, timeout: float = 15) -> tuple[bool, str]:
    """BCH Explorer (Node http, agent:false): Host and Content-Length only, no
    Content-Type; one connection per request."""
    body = json.dumps({"method": "getblockcount", "params": [], "id": int(time.time() * 1000)}).encode()
    req = (f"POST / HTTP/1.1\r\nHost: {node[0]}:{node[1]}\r\nAuthorization: Basic {AUTH}\r\n"
           f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
    with socket.create_connection(node, timeout=timeout) as s:
        s.sendall(req)
        data, deadline = b"", time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([s], [], [], deadline - time.monotonic())
            if not ready:
                break
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    replies = http_responses(data)
    if not replies:
        return False, f"no HTTP reply: {data[:80]!r}"
    try:
        j = json.loads(replies[0]["body"])
    except ValueError:
        return False, f"body not JSON: {replies[0]['body'][:80]!r}"
    if j.get("error") is not None:
        return False, f"error {j['error']}"
    return True, f"result {j.get('result')}"


def transport() -> None:
    cases = [
        ("Fulcrum: 3 pipelined keep-alive requests", "Fulcrum!", lambda n: fulcrum_exchange(n)),
        ("Explorer: no Content-Type, Connection: close", "Explorer!", lambda n: explorer_exchange(n)),
        ("EloPool: getblockchaininfo, no id, no params", "EloPool!",
         lambda n: elopool_exchange(n, b'{"method": "getblockchaininfo"}\n')),
        ("EloPool: getblocktemplate (patched id 0)", "EloPool!",
         lambda n: elopool_exchange(n, b'{"id":0,"method": "getblocktemplate", "params": [{"capabilities": '
                                       b'["coinbasetxn", "workid", "coinbase/append"]}]}\n')),
        ("ASICSeer: getblockchaininfo (patched id 0)", "ASICSeer!",
         lambda n: asicseer_exchange(n, b'{"id":0,"method": "getblockchaininfo", "params": []}\n')),
        ("ASICSeer: getblockcount, no params", "ASICSeer",
         lambda n: asicseer_exchange(n, b'{"id":0,"method": "getblockcount"}\n')),
    ]
    for name, apps, fn in cases:
        row = {"case": name, "apps": apps}
        for label, node in NODES.items():
            try:
                ok, note = fn(node)
            except OSError as e:
                ok, note = False, f"{type(e).__name__}: {e}"
            row[label] = {"ok": ok, "note": note}
        row["verdict"] = ("ok" if row["kth"]["ok"] else
                          "n/a: BCHN fails too" if not row["bchn"]["ok"] else "GAP")
        RESULTS["transport"].append(row)
    # Facts behind the cases above.
    body = json.dumps({"id": 1, "method": "getblockcount", "params": []}).encode()
    for label, node in NODES.items():
        reply = post(node, body)
        RESULTS["setup"][f"{label}_body_ends_with_newline"] = reply["raw"].endswith(b"\n")
        batch = post(node, b'[{"id":1,"method":"getblockcount","params":[]},'
                           b'{"id":2,"method":"getbestblockhash","params":[]}]')
        RESULTS["setup"][f"{label}_batch_reply_unused_by_apps"] = batch["raw"][:120].decode("utf-8", "replace")


# ---- mining -----------------------------------------------------------------

def mining(s: dict) -> None:
    def step(name, node, method, *params):
        result, error = call(node, method, *params)
        RESULTS["mining"].append({"step": name, "node": "kth" if node == KTH else "bchn",
                                  **brief(result, error, 220)})
        return result, error

    template, error = step("light template", KTH, "getblocktemplatelight")
    if error is not None or not template:
        return
    RESULTS["mining"][-1]["merkle_branch_present"] = "merkle" in template
    block, block_hash, coinbase_hex = solve_coinbase_only(template, b"knuth-rpc-e2e")
    RESULTS["mining"].append({"step": "built coinbase-only block on kth's template", "node": "-",
                              "result": block_hash})
    step("decoderawtransaction on the coinbase (EloPool exits if this fails)", KTH,
         "decoderawtransaction", coinbase_hex)
    step("decoderawtransaction on the coinbase", BCHN, "decoderawtransaction", coinbase_hex)
    start = call(KTH, "getblockcount")[0]
    step("submitblocklight the block", KTH, "submitblocklight", block, template["job_id"])
    time.sleep(3)
    step("submitblock (classic) the same block", KTH, "submitblock", block)
    time.sleep(3)
    after = call(KTH, "getblockcount")[0]
    RESULTS["mining"].append({"step": f"kth height before {start}, after both submits {after}",
                              "node": "kth", "result": "grew" if after and start and after > start
                              else "did not grow"})
    step("control: submitblock the very same block to BCHN", BCHN, "submitblock", block)
    bchn_tip = call(BCHN, "getbestblockhash")[0]
    RESULTS["mining"].append({"step": "BCHN tip is the submitted block", "node": "bchn",
                              "result": bchn_tip == block_hash})
    synced = wait_kth_height(start + 1, 120)
    RESULTS["mining"].append({"step": "kth then syncs that block from BCHN over P2P", "node": "kth",
                              "result": synced})
    step("sendrawtransaction a fresh tx", KTH, "sendrawtransaction", s["fresh_tx_send"])
    step("sendrawtransaction the same tx", BCHN, "sendrawtransaction", s["fresh_tx_send"])


# ---- report -------------------------------------------------------------------

def report() -> str:
    lines = ["# Knuth RPC end-to-end (kth, no proxy) vs BCHN 29.2.0 on regtest", ""]
    for k, v in RESULTS["setup"].items():
        if k != "kth_log":
            lines.append(f"- {k}: `{v}`")
    lines += ["", "## Node calls", "", "| Call | Apps (! = fatal) | Verdict | kth | BCHN |", "|---|---|---|---|---|"]
    for r in RESULTS["methods"]:
        cell = lambda d: json.dumps(d)[:180].replace("|", "\\|")
        lines.append(f"| `{r['call']}` | {r['apps']} | **{r['verdict']}** | {cell(r['kth'])} | {cell(r['bchn'])} |")
    lines += ["", "## Client transports (each client's exact bytes and read logic)", "",
              "| Case | Apps | Verdict | kth | BCHN |", "|---|---|---|---|---|"]
    for r in RESULTS["transport"]:
        lines.append(f"| {r['case']} | {r['apps']} | **{r['verdict']}** | {r['kth']['note']} | {r['bchn']['note']} |")
    lines += ["", "## Mining", "", "| Step | Node | Result |", "|---|---|---|"]
    for r in RESULTS["mining"]:
        detail = r.get("error") or r.get("result")
        extra = f" (merkle branch: {r['merkle_branch_present']})" if "merkle_branch_present" in r else ""
        lines.append(f"| {r['step']} | {r['node']} | {json.dumps(detail)[:200].replace('|', '/')}{extra} |")
    gaps = [r["call"] for r in RESULTS["methods"] if r["verdict"].startswith("GAP")]
    gaps += [r["case"] for r in RESULTS["transport"] if r["verdict"] == "GAP"]
    lines += ["", f"## {len(gaps)} gaps", ""] + [f"- {g}" for g in gaps]
    if "kth_log" in RESULTS["setup"]:
        lines += ["", "## kth log tail", "", "```", RESULTS["setup"]["kth_log"], "```"]
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        s = setup()
        RESULTS["setup"]["data"] = {k: v for k, v in s.items() if not k.startswith("fresh")}
        for name, phase in (("methods", lambda: methods(s)), ("transport", transport),
                            ("mining", lambda: mining(s))):
            try:
                phase()
            except Exception as error:  # one broken phase must not hide the others
                RESULTS["setup"][f"{name}_crashed"] = f"{type(error).__name__}: {error}"
    finally:
        RESULTS["setup"].setdefault("kth_log", kth_log(80))
        (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1, default=str))
        text = report()
        (OUT / "results.md").write_text(text)
        print(text)


if __name__ == "__main__":
    main()
