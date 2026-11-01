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
RESULTS: dict = {"setup": {}, "methods": [], "transport": [], "mining": [], "rows": []}


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
                    "-listen=1", "-txindex=1", "-fallbackfee=0.0001", "-allowunconnectedmining",
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
                    "--rpc.user", "e2e", "--rpc.password", "e2e",
                    "--log.debug_file", "/data/e2e/debug.log", "--log.error_file", "/data/e2e/error.log"],
                   check=True)
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

def wallet_address() -> str:
    try:
        return cli("getnewaddress")
    except subprocess.CalledProcessError:
        cli("createwallet", "e2e")  # no wallet loaded yet; BCHN may load a default one
        return cli("getnewaddress")


def make_chain() -> dict:
    """BCHN only: a mined chain with a block of wallet transactions."""
    addr = wallet_address()
    cli("generatetoaddress", "110", addr)
    others = [cli("getnewaddress") for _ in range(3)]
    confirmed = [cli("sendtoaddress", a, "1.5") for a in others]
    tx_block = json.loads(cli("generatetoaddress", "1", addr))[0]
    cli("generatetoaddress", "2", addr)
    block = json.loads(cli("getblock", tx_block, "1"))
    return {"addr": addr, "others": others, "height": int(cli("getblockcount")),
            "tx_block": tx_block, "tx_block_height": block["height"],
            "coinbase_txid": block["tx"][0], "confirmed_txid": confirmed[0], "confirmed": confirmed}


def make_mempool(s: dict) -> dict:
    """BCHN only: two relayed mempool transactions and two signed, unsent ones.
    One output of the confirmed transaction is locked first so gettxout has an
    unspent output to look up."""
    s["unspent_n"] = next(n for n in range(4)
                          if cli("gettxout", s["confirmed_txid"], str(n)) not in ("", "null"))
    cli("lockunspent", "false", json.dumps([{"txid": s["confirmed_txid"], "vout": s["unspent_n"]}]))
    s["mempool_txids"] = [cli("sendtoaddress", a, "0.25") for a in s["others"][:2]]

    def fresh_tx() -> str:
        utxo = next(u for u in json.loads(cli("listunspent", "1"))
                    if u["amount"] >= 1 and u["txid"] not in s["confirmed"])
        cli("lockunspent", "false", json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]))
        raw = cli("createrawtransaction",
                  json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]),
                  json.dumps({cli("getnewaddress"): round(utxo["amount"] - 0.001, 8)}))
        return json.loads(cli("signrawtransactionwithwallet", raw))["hex"]

    s["fresh_tx_send"], s["fresh_tx_test"], s["fresh_tx_row5"] = fresh_tx(), fresh_tx(), fresh_tx()
    s["fresh_tx_row4"] = fresh_tx()
    s["fresh_tx_row11"] = fresh_tx()
    s["tip"] = cli("getbestblockhash")
    return s


def setup() -> dict:
    start_bchn()
    s = make_chain()
    start_kth()
    synced = wait_kth_height(s["height"], 900)
    RESULTS["setup"]["kth_synced_bchn_chain"] = synced
    RESULTS["setup"]["bchn_height"] = s["height"]
    s = make_mempool(s)
    time.sleep(15)  # time for kth to hear about the relayed transactions
    return s


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
    accepted, error = step("submitblocklight the block", KTH, "submitblocklight", block, template["job_id"])
    s["row1"] = {"submitblocklight": accepted if error is None else error, "accepted": error is None and
                 accepted is None, "kth tip": call(KTH, "getbestblockhash")[0], "block": block_hash,
                 "kth height": call(KTH, "getblockcount")[0], "template height": template["height"]}
    # Row 2: BCHN hears of the block from kth alone, before it is given it below.
    reached = False
    if s["row1"]["accepted"]:
        for _ in range(20):
            if call(BCHN, "getbestblockhash")[0] == block_hash:
                reached = True
                break
            time.sleep(1)
    s["row2"] = {"kth accepted it": s["row1"]["accepted"], "BCHN tip is the block": reached,
                 "BCHN tip": call(BCHN, "getbestblockhash")[0], "block": block_hash}
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


# ---- #773 rows: each client's own acceptance of the reply ------------------------

CASHADDR = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def cashaddr_polymod(values) -> int:
    c = 1
    for d in values:
        c0 = c >> 35
        c = ((c & 0x07ffffffff) << 5) ^ d
        for bit, gen in enumerate((0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4, 0xae2eabe2a8, 0x1e4f43e470)):
            if c0 & (1 << bit):
                c ^= gen
    return c ^ 1


def cashaddr_convert(data, frm: int, to: int, pad: bool) -> list:
    acc = bits = 0
    out = []
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            out.append((acc >> bits) & ((1 << to) - 1))
    if pad and bits:
        out.append((acc << (to - bits)) & ((1 << to) - 1))
    return out


def cashaddr_retype(address: str, new_type: int) -> str:
    """The same hash under another CashAddr type (0 P2PKH, 1 P2SH, 2 and 3
    their token-aware forms)."""
    prefix, data = address.split(":")
    values = [CASHADDR.index(c) for c in data][:-8]
    raw = cashaddr_convert(values, 5, 8, False)
    raw[0] = (new_type << 3) | (raw[0] & 0x07)
    payload = cashaddr_convert(raw, 8, 5, True)
    mod = cashaddr_polymod([ord(x) & 0x1f for x in prefix] + [0] + payload + [0] * 8)
    return prefix + ":" + "".join(CASHADDR[v] for v in payload + [(mod >> 5 * (7 - i)) & 31 for i in range(8)])


def raw_exchange(node, request: bytes, timeout: float = 15) -> bytes:
    with socket.create_connection(node, timeout=timeout) as sock:
        sock.sendall(request)
        data, deadline = b"", time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([sock], [], [], deadline - time.monotonic())
            if not ready:
                break
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if http_responses(data) and not http_responses(data)[0].get("short"):
                break
    return data


def asicseer_json(node, body: bytes):
    """ASICseer's json_rpc_call, keeping the parsed reply: (ok, note, json)."""
    with socket.create_connection(node, timeout=15) as sock:
        sock.sendall(ckpool_request(node, body))
        r = LineReader(sock)
        n, line = r.read_line(15)
        if n < 1 or line[:15].lower() != b"http/1.1 200 ok":
            return False, f"status line {line[:60]!r}", None
        length = -1
        while length < 0:
            n, line = r.read_line(15)
            if n < 1:
                return False, "no Content-Length line", None
            if line[:16].lower() == b"content-length: ":
                length = int(line[16:].strip())
                n, _ = r.read_line(15)
                if n != 1:
                    return False, "no blank line right after Content-Length", None
        try:
            return True, "read", json.loads(r.read_exact(length, 15))
        except ValueError as error:
            return False, f"body does not parse: {error}", None


def row(number: int, client: str, check: str, ok: bool, detail) -> None:
    RESULTS["rows"].append({"row": number, "client": client, "check": check,
                            "verdict": "ok" if ok else "GAP",
                            "detail": detail if isinstance(detail, str) else json.dumps(detail)[:400]})


def row5_merkle(s: dict) -> None:
    """A miner on getblocktemplatelight builds a block that carries a
    transaction: the coinbase folded with the template's merkle branch must
    give the root of the block it submits, which BCHN then accepts."""
    client, check = "light-template miner", "block with a transaction, root from `merkle`, accepted by BCHN"
    txid, _ = call(KTH, "sendrawtransaction", s["fresh_tx_row5"])
    call(BCHN, "sendrawtransaction", s["fresh_tx_row5"])
    time.sleep(7)  # past kth's template refresh window under mempool change
    template, error = call(KTH, "getblocktemplatelight")
    if error is not None or not template:
        return row(5, client, check, False, {"template": error})
    branch = template.get("merkle")
    if not branch:
        return row(5, client, check, False, {"merkle": branch, "kth mempool": call(KTH, "getrawmempool")[0]})
    # The template's transactions, in canonical order (txid bytes as stored).
    pool = sorted(call(KTH, "getrawmempool")[0] or [], key=lambda t: bytes.fromhex(t)[::-1])
    txs = [bytes.fromhex(call(BCHN, "getrawtransaction", t, False)[0]) for t in pool]
    subsidy = (50 * 100_000_000) >> (template["height"] // 150)
    base = coinbase(template["height"], subsidy, b"knuth-rpc-e2e row 5")
    root = sha256d(base)
    for sibling in branch:
        root = sha256d(root + bytes.fromhex(sibling)[::-1])
    prev = bytes.fromhex(template["previousblockhash"])[::-1]
    bits = int(template["bits"], 16)
    target = (bits & 0xFFFFFF) << (8 * ((bits >> 24) - 3))
    when = max(template["curtime"], template["mintime"])
    for nonce in range(1 << 32):
        header = struct.pack("<I", template["version"]) + prev + root + struct.pack("<III", when, bits, nonce)
        if int.from_bytes(sha256d(header)[::-1], "big") <= target:
            break
    block = header + bytes([1 + len(txs)]) + base + b"".join(txs)
    accepted, error = call(BCHN, "submitblock", block.hex())
    tip = call(BCHN, "getbestblockhash")[0]
    ok = error is None and accepted is None and tip == sha256d(header)[::-1].hex()
    row(5, client, check, ok, {"merkle": branch, "kth mempool": pool, "first is a txid": branch[0] in pool,
                               "bchn submitblock": accepted if error is None else error,
                               "bchn tip is the block": tip == sha256d(header)[::-1].hex(),
                               "sendrawtransaction to kth": txid})
    wait_kth_height(template["height"], 120)


def row6_validateaddress(s: dict) -> None:
    """ASICseer's validate_address on its payout address: isvalid true, a hex
    scriptPubKey for the coinbase output, and isscript. The scriptPubKey must
    be the one the address pays to (BCHN's answer for the same address)."""
    client = "ASICseer pool (validate_address)"
    plain = s["addr"]
    for label, address in (("P2PKH", plain), ("token-aware P2PKH", cashaddr_retype(plain, 2)),
                           ("P2SH", cashaddr_retype(plain, 1)), ("token-aware P2SH", cashaddr_retype(plain, 3))):
        body = json.dumps({"method": "validateaddress", "params": [address]}).encode() + b"\n"
        ok, note, reply = asicseer_json(KTH, body)
        if not ok:
            # ASICseer's bare-LF request and its line reader need #770 and #771;
            # send the same body over CRLF so this row is judged on its reply.
            transport = note
            reply = post(KTH, body)["json"]
            ok, note = isinstance(reply, dict), f"over CRLF; ASICseer's own transport: {transport}"
        bchn = call(BCHN, "validateaddress", address)[0] or {}
        result = (reply or {}).get("result") or {}
        spk = result.get("scriptPubKey")
        accepted = (ok and result.get("isvalid") is True and isinstance(spk, str) and len(spk) % 2 == 0
                    and "isscript" in result)
        row(6, client, f"{label} {address}", accepted and spk == bchn.get("scriptPubKey")
            and result.get("isscript") == bchn.get("isscript") and result.get("address") == bchn.get("address"),
            {"kth": reply.get("result") if isinstance(reply, dict) else None, "note": note, "bchn": bchn})
    wrong = "bitcoincash:qr7fzmep8g7h7ymfxy74lgc0v950j3r2959lhtxxsl"  # a mainnet address on regtest
    reply = call(KTH, "validateaddress", wrong)
    row(6, client, "mainnet address on regtest is not valid", reply[1] is None and reply[0] == {"isvalid": False},
        {"kth": reply})


def row8_blockchaininfo(s: dict) -> None:
    """Fulcrum's GetChainInfoTask parse, the Explorer's blocks == headers gate,
    and the remaining fields, against the same chain on BCHN."""
    info, error = call(KTH, "getblockchaininfo")
    bchn = call(BCHN, "getblockchaininfo")[0] or {}
    if error is not None or not isinstance(info, dict):
        return row(8, "Fulcrum", "getblockchaininfo parses", False, {"kth": error})
    fulcrum = (isinstance(info.get("blocks"), int) and info["blocks"] >= 0 and bool(info.get("chain"))
               and len(bytes.fromhex(info.get("bestblockhash", ""))) == 32)
    row(8, "Fulcrum", "GetChainInfoTask parse; initialblockdownload false at the tip",
        fulcrum and info.get("initialblockdownload") is False,
        {f: info.get(f) for f in ("blocks", "chain", "bestblockhash", "initialblockdownload")})
    row(8, "BCH Explorer", "indexer gate blocks == headers", info.get("blocks") == info.get("headers"),
        {"blocks": info.get("blocks"), "headers": info.get("headers")})
    same = {f: (info.get(f), bchn.get(f)) for f in ("mediantime", "chainwork")}
    row(8, "Fulcrum, pool status pages", "mediantime and chainwork equal BCHN's on the same chain",
        all(a == b and a is not None for a, b in same.values()), same)
    kinds = {"verificationprogress": (int, float), "size_on_disk": int, "warnings": str}
    present = {f: info.get(f) for f in kinds}
    row(8, "Fulcrum, pool status pages", "verificationprogress, size_on_disk, warnings present",
        all(isinstance(info.get(f), t) for f, t in kinds.items()), present)


def row15_help(s: dict) -> None:
    """Fulcrum's connection check: `help ["help"]` must succeed."""
    body = json.dumps({"id": 1, "method": "help", "params": ["help"]}).encode() + b"\r\n"
    request = (f"POST / HTTP/1.1\r\nHost: {KTH[0]}:{KTH[1]}\r\nContent-Type: application/json-rpc\r\n"
               f"Authorization: Basic {AUTH}\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
    replies = http_responses(raw_exchange(KTH, request))
    reply = json.loads(replies[0]["body"]) if replies else None
    ok = isinstance(reply, dict) and reply.get("error") is None and "result" in reply and reply.get("id") == 1
    row(15, "Fulcrum", "help [\"help\"] succeeds", ok, reply)
    listing = call(KTH, "help")
    row(15, "operator", "help lists the methods", listing[1] is None and "getblockcount" in (listing[0] or ""),
        listing)


def row16_confirmations(s: dict) -> None:
    """Fulcrum's verbose getblockheader: confirmations and height."""
    tip = call(KTH, "getblockcount")[0]
    header, error = call(KTH, "getblockheader", s["tx_block"], True)
    bchn = call(BCHN, "getblockheader", s["tx_block"], True)[0] or {}
    ok = (error is None and isinstance(header, dict) and isinstance(header.get("confirmations"), int)
          and header.get("height") == s["tx_block_height"]
          and header["confirmations"] == tip - s["tx_block_height"] + 1 == bchn.get("confirmations"))
    row(16, "Fulcrum", "getblockheader [hash, true] confirmations", ok,
        {"kth": header if error is None else error, "bchn confirmations": bchn.get("confirmations"), "tip": tip})


def row18_estimatefee(s: dict) -> None:
    """Fulcrum's one-argument estimatefee for nodes it does not know to take
    none; it passes the number back to the wallet."""
    fee, error = call(KTH, "estimatefee", 6)
    ok = error is None and isinstance(fee, (int, float)) and (fee >= 0 or fee == -1)
    row(18, "Fulcrum", "estimatefee [6] returns a fee rate", ok,
        {"kth": fee if error is None else error, "bchn estimatefee []": call(BCHN, "estimatefee")[0]})


def row19_networkhashps(s: dict) -> None:
    """The Explorer's indexer and mining page requests; on the same chain the
    rate must equal BCHN's."""
    for params in ([1, s["tx_block_height"]], [1, 1], [1008]):
        rate, error = call(KTH, "getnetworkhashps", *params)
        bchn = call(BCHN, "getnetworkhashps", *params)[0]
        ok = (error is None and isinstance(rate, (int, float)) and isinstance(bchn, (int, float))
              and abs(rate - bchn) <= 1e-9 * max(abs(bchn), 1e-30))
        row(19, "BCH Explorer", f"getnetworkhashps {json.dumps(params)}", ok,
            {"kth": rate if error is None else error, "bchn": bchn})


def keys_missing(kth, bchn, skip=()) -> list:
    """Keys BCHN's reply has that kth's lacks, recursively, ignoring `skip`."""
    missing = []
    if isinstance(bchn, dict):
        if not isinstance(kth, dict):
            return ["<object>"]
        for key, value in bchn.items():
            if key in skip:
                continue
            if key not in kth:
                missing.append(key)
            else:
                missing += [f"{key}.{m}" for m in keys_missing(kth[key], value, skip)]
    elif isinstance(bchn, list) and bchn and isinstance(kth, list) and kth:
        missing += [f"[0].{m}" for m in keys_missing(kth[0], bchn[0], skip)]
    return missing


def row7_decoderawtransaction(s: dict) -> None:
    """EloPool's startup call: decoderawtransaction on its coinbase must answer
    HTTP 200 with JSON, or EloPool exits. The shape is set against BCHN's for
    the same transaction (#370 asks for that comparison)."""
    template = call(KTH, "getblocktemplatelight")[0] or {"height": 1}
    coinbase_hex = coinbase(template["height"], 50 * 100_000_000, b"knuth-rpc-e2e row 7").hex()
    body = json.dumps({"method": "decoderawtransaction", "params": [coinbase_hex]}).encode()
    ok, note = elopool_exchange(KTH, body + b"\n")
    reply = post(KTH, body)["json"] or {}
    if not ok:
        note = f"over CRLF; EloPool's own transport: {note}"
    result = reply.get("result")
    row(7, "EloPool", "decoderawtransaction on its coinbase answers JSON", isinstance(result, dict),
        {"note": note, "kth": result if result is not None else reply.get("error")})
    for label, txid in (("coinbase", s["coinbase_txid"]), ("spend", s["confirmed_txid"])):
        raw = call(BCHN, "getrawtransaction", txid, False)[0]
        kth, error = call(KTH, "decoderawtransaction", raw)
        bchn = call(BCHN, "decoderawtransaction", raw)[0]
        missing = keys_missing(kth, bchn, skip=("asm",)) if error is None else ["<error>"]
        same = error is None and all(kth.get(k) == bchn.get(k) for k in ("txid", "size", "locktime", "version"))
        outs = error is None and [(o["value"], o["scriptPubKey"]["hex"], o["scriptPubKey"].get("addresses"))
                                  for o in kth["vout"]] == [(o["value"], o["scriptPubKey"]["hex"],
                                                            o["scriptPubKey"].get("addresses")) for o in bchn["vout"]]
        row(7, "shape vs bitcoind (#370)", f"decoderawtransaction {label}: same fields and values",
            same and outs and not missing, {"missing": missing, "kth": kth if error is None else error})


def row9_chain(s: dict) -> None:
    """EloPool and ASICseer map `chain` to the payout address prefix."""
    known = ("main", "test", "test4", "scale", "chip", "regtest")
    for method in ("getblockchaininfo", "getmininginfo"):
        kth = (call(KTH, method)[0] or {}).get("chain")
        bchn = (call(BCHN, method)[0] or {}).get("chain")
        row(9, "EloPool, ASICseer pool", f"{method} chain is a name the pools map", kth in known and kth == bchn,
            {"kth": kth, "bchn": bchn})


def row13_gettxout(s: dict) -> None:
    """The Explorer's gettxout [txid, n] and [txid, n, false]: an unspent output
    is described, a spent one is null."""
    txid, n = s["confirmed_txid"], s["unspent_n"]
    for params in ([txid, n], [txid, n, False]):
        kth, error = call(KTH, "gettxout", *params)
        bchn = call(BCHN, "gettxout", *params)[0]
        same = error is None and isinstance(kth, dict) and isinstance(bchn, dict) and all(
            kth.get(k) == bchn.get(k) for k in ("bestblock", "confirmations", "value", "coinbase")) and \
            kth["scriptPubKey"]["hex"] == bchn["scriptPubKey"]["hex"]
        row(13, "BCH Explorer", f"gettxout {json.dumps(params)[:24]}... unspent", same,
            {"kth": kth if error is None else error, "bchn": bchn,
             "missing": keys_missing(kth, bchn, skip=("asm",)) if error is None else None})
    # An output spent in a block: the confirmed transaction's own input.
    spent = (call(BCHN, "getrawtransaction", txid, True)[0] or {}).get("vin", [{}])[0]
    kth, error = call(KTH, "gettxout", spent.get("txid"), spent.get("vout"))
    bchn = call(BCHN, "gettxout", spent.get("txid"), spent.get("vout"))[0]
    row(13, "BCH Explorer", "gettxout on an output spent in a block is null",
        error is None and kth is None and bchn is None, {"kth": kth if error is None else error, "bchn": bchn})


def row14_getnetworkinfo(s: dict) -> None:
    """Fulcrum's node detection (subversion, version, relayfee, warnings), the
    Explorer's subversion, and pool status pages' connections."""
    info, error = call(KTH, "getnetworkinfo")
    bchn = call(BCHN, "getnetworkinfo")[0] or {}
    if error is not None or not isinstance(info, dict):
        return row(14, "Fulcrum", "getnetworkinfo answers", False, error)
    fulcrum = (isinstance(info.get("subversion"), str) and info["subversion"].startswith("/")
               and isinstance(info.get("version"), int) and info["version"] > 0
               and isinstance(info.get("relayfee"), (int, float)) and isinstance(info.get("warnings"), str))
    row(14, "Fulcrum", "subversion, version, relayfee, warnings", fulcrum,
        {f: info.get(f) for f in ("subversion", "version", "relayfee", "warnings")})
    row(14, "pool status pages", "connections counts the BCHN peer",
        isinstance(info.get("connections"), int) and info["connections"] >= 1,
        {"connections": info.get("connections"), "missing vs bitcoind": keys_missing(
            info, bchn, skip=("networks", "localaddresses", "timeoffset", "excessutxocharge",
                              "connections_in", "connections_out"))})


def row20_getchaintips(s: dict) -> None:
    """The Explorer's getchaintips at startup: the active tip is BCHN's."""
    tips, error = call(KTH, "getchaintips")
    bchn = call(BCHN, "getchaintips")[0] or []
    active = [t for t in (tips or []) if isinstance(t, dict) and t.get("status") == "active"]
    bchn_active = [t for t in bchn if t.get("status") == "active"]
    row(20, "BCH Explorer", "getchaintips lists the active tip", error is None and len(active) == 1
        and active[0].get("hash") == bchn_active[0]["hash"] and active[0].get("height") == bchn_active[0]["height"],
        {"kth": tips if error is None else error, "bchn": bchn})


def compact_size(n: int) -> bytes:
    if n < 0xfd:
        return bytes([n])
    if n <= 0xffff:
        return b"\xfd" + struct.pack("<H", n)
    return b"\xfe" + struct.pack("<I", n)


def row4_getblocktemplate(s: dict) -> None:
    """ASICseer's and EloPool's getblocktemplate requests; then a block built
    the way they build it (their coinbase, the template's transactions in its
    order) must be accepted by BCHN."""
    for client, request in (
            ("ASICseer pool", {"capabilities": ["coinbasetxn", "workid", "coinbase/append"], "rules": []}),
            ("EloPool", {"capabilities": ["coinbasetxn", "workid", "coinbase/append"]})):
        template, error = call(KTH, "getblocktemplate", request)
        fields = ("previousblockhash", "target", "version", "curtime", "bits", "height", "coinbasevalue",
                  "transactions")
        ok = error is None and isinstance(template, dict) and all(f in template for f in fields) and \
            template["version"] != 0 and all({"data", "txid", "hash"} <= set(t) for t in template["transactions"])
        row(4, client, "getblocktemplate has the fields the pool reads", ok,
            {"kth": {k: v for k, v in (template or {}).items() if k != "transactions"} if error is None else error,
             "transactions": len((template or {}).get("transactions", []))})
    # A transaction for the template to carry: kth's mempool only has what it
    # is sent (relay from peers is #371).
    call(KTH, "sendrawtransaction", s["fresh_tx_row4"])
    call(BCHN, "sendrawtransaction", s["fresh_tx_row4"])
    time.sleep(7)  # past kth's template refresh window under mempool change
    template, error = call(KTH, "getblocktemplate", {"capabilities": ["coinbasetxn", "workid", "coinbase/append"]})
    if error is not None or not template:
        return row(4, "ASICseer pool, EloPool", "block from kth's template accepted by BCHN", False, error)
    txs = [bytes.fromhex(t["data"]) for t in template["transactions"]]
    fees = sum(t.get("fee", 0) for t in template["transactions"])
    subsidy = (50 * 100_000_000) >> (template["height"] // 150)
    base = coinbase(template["height"], subsidy + fees, b"knuth-rpc-e2e row 4")
    level = [sha256d(base)] + [sha256d(t) for t in txs]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    prev = bytes.fromhex(template["previousblockhash"])[::-1]
    bits = int(template["bits"], 16)
    target = (bits & 0xFFFFFF) << (8 * ((bits >> 24) - 3))
    when = max(template["curtime"], template["mintime"])
    for nonce in range(1 << 32):
        header = struct.pack("<I", template["version"]) + prev + level[0] + struct.pack("<III", when, bits, nonce)
        if int.from_bytes(sha256d(header)[::-1], "big") <= target:
            break
    block = header + compact_size(1 + len(txs)) + base + b"".join(txs)
    accepted, error = call(BCHN, "submitblock", block.hex())
    tip = call(BCHN, "getbestblockhash")[0]
    row(4, "ASICseer pool, EloPool", "block built from kth's getblocktemplate accepted by BCHN",
        error is None and accepted is None and tip == sha256d(header)[::-1].hex(),
        {"transactions": len(txs), "fees": fees, "bchn submitblock": accepted if error is None else error,
         "bchn tip is the block": tip == sha256d(header)[::-1].hex()})
    wait_kth_height(template["height"], 120)


def row12_block_reads(s: dict) -> None:
    """Fulcrum's raw block and mempool transaction reads, and the Explorer's
    verbose block and transaction reads."""
    blk = s["tx_block"]
    raw, error = call(KTH, "getblock", blk, False)
    bchn_raw = call(BCHN, "getblock", blk, False)[0]
    ok = error is None and isinstance(raw, str) and sha256d(bytes.fromhex(raw[:160]))[::-1].hex() == blk \
        and raw == bchn_raw
    row(12, "Fulcrum", "getblock [hash, false]: the block, its header hashing to the hash", ok,
        {"kth": (raw[:64] + "...") if isinstance(raw, str) else error})
    pool = call(KTH, "getrawmempool", False)[0] or []
    for txid in pool[:2]:
        kth, error = call(KTH, "getrawtransaction", txid, False)
        bchn = call(BCHN, "getrawtransaction", txid, False)[0]
        # The transaction is the one asked for when its bytes hash to the txid.
        # Compared with BCHN's copy only when BCHN holds it: a transaction sent
        # to kth alone is in no other mempool.
        ok = error is None and isinstance(kth, str) and sha256d(bytes.fromhex(kth))[::-1].hex() == txid \
            and (bchn is None or kth == bchn)
        row(12, "Fulcrum", f"getrawtransaction [{txid[:12]}..., false] for a mempool tx", ok,
            {"kth": (kth[:40] + "...") if isinstance(kth, str) else error, "in BCHN's mempool": bchn is not None})
    kth, error = call(KTH, "getrawtransaction", s["coinbase_txid"], False)
    row(12, "Fulcrum", "a confirmed tx without its block hash is error -5, which Fulcrum tolerates",
        error is not None and error.get("code") == -5, {"kth": error})
    for verbosity in (1, 2):
        kth, error = call(KTH, "getblock", blk, verbosity)
        bchn = call(BCHN, "getblock", blk, verbosity)[0]
        if error is None and not isinstance(kth, dict):
            error = {"not an object": str(kth)[:40]}
        missing = keys_missing(kth, bchn, skip=("asm", "fee", "ablastate")) if error is None else ["<error>"]
        same = error is None and all(kth.get(k) == bchn.get(k) for k in (
            "hash", "confirmations", "size", "height", "version", "merkleroot", "time", "mediantime", "nonce",
            "bits", "chainwork", "nTx", "previousblockhash", "nextblockhash"))
        txs = error is None and (kth["tx"] == bchn["tx"] if verbosity == 1 else
                                 [t["txid"] for t in kth["tx"]] == [t["txid"] for t in bchn["tx"]])
        row(12, "BCH Explorer", f"getblock [hash, {verbosity}] fields and values", same and txs and not missing,
            {"missing": missing, "differs": [k for k in ("confirmations", "mediantime", "chainwork", "nextblockhash",
                                                          "size") if error is None and kth.get(k) != bchn.get(k)]})
    kth, error = call(KTH, "getrawtransaction", s["confirmed_txid"], True, blk)
    bchn = call(BCHN, "getrawtransaction", s["confirmed_txid"], True, blk)[0]
    if error is None and not isinstance(kth, dict):
        error = {"not an object": str(kth)[:40]}
    same = error is None and all(kth.get(k) == bchn.get(k) for k in ("txid", "hex", "blockhash", "confirmations",
                                                                      "time", "blocktime", "in_active_chain"))
    missing = keys_missing(kth, bchn, skip=("asm",)) if error is None else ["<error>"]
    row(12, "BCH Explorer", "getrawtransaction [txid, true, blockhash]", same and not missing,
        {"kth": {k: kth.get(k) for k in ("blockhash", "confirmations", "time", "in_active_chain")}
         if error is None else error, "missing": missing})


def row10_relay_in(s: dict) -> None:
    """Transactions relayed by a peer reach kth's mempool: the wallet
    transactions BCHN made right after kth synced, and one it makes now, long
    after, are in kth's mempool too."""
    client = "Fulcrum, BCH Explorer, miners"
    kth = set(call(KTH, "getrawmempool")[0] or [])
    missing = [t for t in s["mempool_txids"] if t not in kth]
    row(10, client, "BCHN's wallet transactions from right after the sync are in kth's mempool",
        not missing, {"kth mempool": sorted(kth), "missing": missing})
    late = cli("sendtoaddress", s["others"][2], "0.1")
    reached = False
    for _ in range(30):
        if late in (call(KTH, "getrawmempool")[0] or []):
            reached = True
            break
        time.sleep(1)
    row(10, client, "a wallet transaction BCHN makes later reaches kth's mempool", reached,
        {"txid": late, "in kth's mempool": reached})


def row11_relay_out(s: dict) -> None:
    """A transaction sent only to kth reaches BCHN's mempool."""
    txid, error = call(KTH, "sendrawtransaction", s["fresh_tx_row11"])
    reached = False
    for _ in range(30):
        if txid and txid in (call(BCHN, "getrawmempool")[0] or []):
            reached = True
            break
        time.sleep(1)
    row(11, "wallets, Fulcrum (broadcast)", "a transaction sent only to kth reaches BCHN's mempool",
        error is None and reached, {"kth sendrawtransaction": txid if error is None else error,
                                    "in BCHN's mempool": reached})


def row17_rawmempool_verbose(s: dict) -> None:
    """The Explorer's getrawmempool [true]: an object keyed by txid, each with
    fees.base, size and depends."""
    kth, error = call(KTH, "getrawmempool", True)
    bchn = call(BCHN, "getrawmempool", True)[0] or {}
    ok = error is None and isinstance(kth, dict) and kth and all(
        isinstance(v.get("fees", {}).get("base"), (int, float)) and isinstance(v.get("size"), int)
        and isinstance(v.get("depends"), list) for v in kth.values())
    shared = [t for t in kth if t in bchn] if isinstance(kth, dict) else []
    same = all(kth[t]["fees"]["base"] == bchn[t]["fees"]["base"] and kth[t]["size"] == bchn[t]["size"]
               for t in shared)
    row(17, "BCH Explorer", "getrawmempool [true] entries keyed by txid", ok and same,
        {"kth": kth if error is None else error, "shared with BCHN": len(shared)})


def row1_accept_found_block(s: dict) -> None:
    """The mining section's submitblocklight of a block built on kth's own light
    template, read right after the call: null, and kth's tip is that block
    before the same block is given to BCHN."""
    seen = s.get("row1")
    ok = bool(seen) and seen["accepted"] and seen["kth tip"] == seen["block"] \
        and seen["kth height"] == seen["template height"]
    row(1, "light-template miner", "submitblocklight answers null and kth's tip is the block", ok, seen)


def row2_found_block_reaches_peers(s: dict) -> None:
    """The block kth accepted through submitblocklight reaches BCHN from kth
    alone, before the harness gives it to BCHN itself."""
    seen = s.get("row2")
    row(2, "light-template miner (the network learns of its block)",
        "BCHN's tip becomes the block kth accepted", bool(seen) and seen["kth accepted it"]
        and seen["BCHN tip is the block"], seen)


def row3_submitblock(s: dict) -> None:
    """A pool on full templates submits a whole block with submitblock: null,
    and kth's tip is that block. A coinbase-only block on the current tip, so it
    does not depend on what kth's mempool holds. BCHN is then given it too, so
    the two stay on one chain."""
    client = "ASICseer pool, EloPool (submitblock)"
    template, error = call(KTH, "getblocktemplatelight")
    if error is not None or not template:
        return row(3, client, "getblocktemplatelight answers", False, error)
    block, block_hash, _ = solve_coinbase_only(template, b"knuth-rpc-e2e row 3")
    accepted, error = call(KTH, "submitblock", block)
    tip = call(KTH, "getbestblockhash")[0]
    row(3, client, "submitblock answers null and kth's tip is the block",
        error is None and accepted is None and tip == block_hash,
        {"submitblock": accepted if error is None else error, "kth tip": tip, "block": block_hash})
    call(BCHN, "submitblock", block)


def rows(s: dict) -> None:
    for check in (row10_relay_in, row17_rawmempool_verbose, row11_relay_out,
                  row6_validateaddress, row7_decoderawtransaction, row8_blockchaininfo, row9_chain,
                  row12_block_reads, row13_gettxout, row14_getnetworkinfo, row15_help, row16_confirmations,
                  row18_estimatefee, row19_networkhashps, row20_getchaintips, row5_merkle, row4_getblocktemplate,
                  row1_accept_found_block, row2_found_block_reaches_peers, row3_submitblock):
        try:
            check(s)
        except Exception as error:  # one broken check must not hide the others
            row(0, check.__name__, "crashed", False, f"{type(error).__name__}: {error}")


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
    lines += ["", "## #773 rows: each client's own acceptance of kth's reply", "",
              "| Row | Client | Check | Verdict | Detail |", "|---|---|---|---|---|"]
    for r in RESULTS["rows"]:
        lines.append(f"| {r['row']} | {r['client']} | {r['check']} | **{r['verdict']}** | "
                     f"{r['detail'][:400].replace('|', '/')} |")
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
        RESULTS["setup"]["data"] = {k: v for k, v in s.items()
                                    if not k.startswith("fresh") and k not in ("others", "confirmed")}
        for name, phase in (("methods", lambda: methods(s)), ("transport", transport),
                            ("mining", lambda: mining(s)), ("rows", lambda: rows(s))):
            try:
                phase()
            except Exception as error:  # one broken phase must not hide the others
                RESULTS["setup"][f"{name}_crashed"] = f"{type(error).__name__}: {error}"
    finally:
        RESULTS["setup"].setdefault("kth_log", kth_log(80))
        # kth logs to a file as a daemon; stopped first so what it buffered is written.
        subprocess.run(["docker", "stop", "-t", "15", "kth"], capture_output=True)
        subprocess.run(["docker", "cp", "kth:/data/e2e/debug.log", str(OUT / "kth-debug.log")],
                       capture_output=True)
        (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1, default=str))
        text = report()
        (OUT / "results.md").write_text(text)
        print(text)


if __name__ == "__main__":
    main()
