#!/usr/bin/env python3
"""TEST ONLY. scantxoutset on kth against BCHN, end to end.

A BCHN regtest node makes P2PKH, P2SH, P2SH32 and token outputs (fungible, NFT
none/mutable/minting, both, and a covenant holding a mutable NFT as PHOTON keeps
its mining state); kth, built from the branch, syncs them over P2P. Then:

  - access: off by default (-32601 naming the setting); on, loopback answered and
    a caller on the runner's own address refused until rpc.scantxoutset_caller
    names it; one startup warning with the RPC bound beyond loopback;
  - the same scan objects to both nodes (raw, addr in each form, tok, several at
    once, the object form) and the unspents, totals and token totals compared;
  - errors to both nodes, codes compared;
  - status during a scan and abort, on both nodes;
  - a set of about a million outputs, then blocks mined on BCHN while kth scans
    it back to back: how long kth takes to connect each block and to answer
    getblocktemplate, against the same with no scan running;
  - kth's debug log searched for every script, address and category scanned.

Writes <out>/results.json and <out>/results.md. Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-out")
BCHN_DIR = Path("/tmp/bchn-scan-regtest")
BCHN = ("127.0.0.1", 18443)
KTH_PORT = 19332
KTH = ("127.0.0.1", KTH_PORT)
KTH_P2P_PORT = 18555
AUTH = base64.b64encode(b"e2e:e2e").decode()
FANOUT_TXS = int(os.environ.get("SCAN_FANOUT_TXS") or 40)
OUTPUTS_PER_TX = int(os.environ.get("SCAN_OUTPUTS_PER_TX") or 25000)
RESULTS: dict = {"setup": {}, "access": [], "compare": [], "errors": [], "status_abort": [],
                 "load": {}, "privacy": [], "versions": {}}


# ---- JSON-RPC -----------------------------------------------------------------

def post(node, body: bytes, timeout: float = 600) -> dict:
    request = Request(f"http://{node[0]}:{node[1]}/", body,
                      {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read() or b""
    except (URLError, OSError) as error:
        return {"status": None, "transport": f"{type(error).__name__}: {error}", "raw": b"", "json": None,
                "ms": round((time.monotonic() - started) * 1000)}
    try:
        parsed = json.loads(raw, parse_float=Decimal) if raw else None
    except ValueError:
        parsed = None
    return {"status": status, "raw": raw, "json": parsed, "ms": round((time.monotonic() - started) * 1000, 1)}


_next_id = 0


def call(node, method: str, *params, timeout: float = 600):
    global _next_id
    _next_id += 1
    reply = post(node, json.dumps({"id": _next_id, "method": method, "params": list(params)}).encode(),
                 timeout)
    j = reply["json"]
    if isinstance(j, dict):
        return j.get("result"), j.get("error")
    return None, {"transport": reply.get("transport"), "status": reply["status"],
                  "raw": reply["raw"][:300].decode("utf-8", "replace")}


def timed_call(node, method: str, *params, timeout: float = 600):
    global _next_id
    _next_id += 1
    reply = post(node, json.dumps({"id": _next_id, "method": method, "params": list(params)}).encode(),
                 timeout)
    j = reply["json"] if isinstance(reply["json"], dict) else {}
    return j.get("result"), j.get("error"), reply["ms"]


def cli(*args) -> str:
    return subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_DIR}", "-rpcuser=e2e",
                           "-rpcpassword=e2e", "-rpcport=18443", "-rpcclienttimeout=1200", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def cli_json(*args):
    return json.loads(cli(*args), parse_float=Decimal)


# ---- nodes --------------------------------------------------------------------

def wait_for(node, name: str, seconds: int = 180) -> None:
    for _ in range(seconds):
        result, error = call(node, "getblockchaininfo", timeout=10)
        if error is None and result:
            return
        time.sleep(1)
    raise SystemExit(f"{name} never answered getblockchaininfo")


def start_bchn() -> None:
    BCHN_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bitcoind", "-regtest", f"-datadir={BCHN_DIR}", "-daemon", "-server",
                    "-rpcuser=e2e", "-rpcpassword=e2e", "-rpcport=18443", "-port=18444",
                    "-listen=1", "-fallbackfee=0.0001", "-acceptnonstdtxn=1",
                    "-blockmaxsize=32000000", "-maxmempool=2000", "-rpcthreads=8",
                    "-rpcworkqueue=64", "-allowunconnectedmining"], check=True)
    wait_for(BCHN, "BCHN")


def kth_args(*, on: bool, bind: str, callers: list[str]) -> list[str]:
    args = ["--init_run", "--network", "regtest", "--node.display", "daemon",
            "--db.directory", "/data/e2e", "--db.db_mode", "full",
            "--net.inbound_port", str(KTH_P2P_PORT), "--net.inbound_connections", "0",
            "--net.outbound_connections", "1", "--net.peer", "127.0.0.1:18444",
            "--rpc.enabled", "true", "--rpc.bind", bind, "--rpc.port", str(KTH_PORT),
            "--rpc.user", "e2e", "--rpc.password", "e2e", "--log.verbose", "true",
            "--log.debug_file", "/data/kth-debug.log", "--log.error_file", "/data/kth-error.log"]
    if on:
        args += ["--rpc.scantxoutset", "true"]
    for caller in callers:
        args += ["--rpc.scantxoutset_caller", caller]
    return args


def start_kth(name: str, **settings) -> None:
    subprocess.run(["docker", "rm", "-f", "kth"], capture_output=True)
    subprocess.run(["docker", "run", "--detach", "--name", "kth", "--network", "host",
                    "--platform", "linux/amd64", "--entrypoint", "/usr/local/bin/kth", "knuth-e2e",
                    *kth_args(**settings)], check=True)
    wait_for(KTH, f"kth ({name})")


def stop_kth(keep_log_as: str | None = None) -> str:
    subprocess.run(["docker", "stop", "-t", "20", "kth"], capture_output=True)
    log = OUT / f"kth-{keep_log_as or 'debug'}.log"
    subprocess.run(["docker", "cp", "kth:/data/kth-debug.log", str(log)], capture_output=True)
    text = log.read_text(errors="replace") if log.exists() else ""
    text += subprocess.run(["docker", "logs", "kth"], capture_output=True, text=True).stdout
    subprocess.run(["docker", "rm", "-f", "kth"], capture_output=True)
    return text


def kth_height() -> int:
    result, _ = call(KTH, "getblockcount", timeout=10)
    return result if isinstance(result, int) else -1


def wait_kth_height(height: int, seconds: float) -> float | None:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if kth_height() >= height:
            return time.monotonic() - started
        time.sleep(0.02)
    return None


def own_address() -> str:
    """This runner's address on its default route: a caller that is not loopback."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    finally:
        probe.close()


# ---- addresses and scripts ------------------------------------------------------

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def polymod(values) -> int:
    c = 1
    for d in values:
        c0 = c >> 35
        c = ((c & 0x07ffffffff) << 5) ^ d
        for bit, gen in enumerate((0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4, 0xae2eabe2a8, 0x1e4f43e470)):
            if c0 & (1 << bit):
                c ^= gen
    return c ^ 1


def convert(data, frm: int, to: int, pad: bool) -> list:
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


def cashaddr(prefix: str, kind: int, hash_bytes: bytes) -> str:
    size = {20: 0, 24: 1, 28: 2, 32: 3, 40: 4, 48: 5, 56: 6, 64: 7}[len(hash_bytes)]
    payload = convert([(kind << 3) | size] + list(hash_bytes), 8, 5, True)
    mod = polymod([ord(x) & 0x1f for x in prefix] + [0] + payload + [0] * 8)
    return prefix + ":" + "".join(CHARSET[v] for v in payload + [(mod >> 5 * (7 - i)) & 31 for i in range(8)])


def cashaddr_hash(address: str) -> tuple[int, bytes]:
    data = address.split(":")[-1]
    raw = convert([CHARSET.index(c) for c in data][:-8], 5, 8, False)
    return raw[0] >> 3, bytes(raw[1:])


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def base58check(payload: bytes) -> str:
    data = payload + sha256d(payload)[:4]
    n = int.from_bytes(data, "big")
    text = ""
    while n:
        n, r = divmod(n, 58)
        text = B58[r] + text
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + text


def script_of(address: str) -> str:
    return cli_json("validateaddress", address)["scriptPubKey"]


def compact(n: int) -> bytes:
    if n < 0xfd:
        return bytes([n])
    if n <= 0xffff:
        return b"\xfd" + n.to_bytes(2, "little")
    return b"\xfe" + n.to_bytes(4, "little")


# ---- the chain ------------------------------------------------------------------

def make_outputs() -> dict:
    """BCHN only: plain, script-hash and token outputs, one block of them."""
    try:
        cli("createwallet", "e2e")
    except subprocess.CalledProcessError:
        pass
    mine = cli("getnewaddress")
    cli("generatetoaddress", "200", mine)

    s: dict = {"mine": mine}
    p = cli("getnewaddress")
    kind, h160 = cashaddr_hash(p)
    s["p2pkh"] = p
    s["p2pkh_bare"] = p.split(":")[1]
    s["p2pkh_token_aware"] = cashaddr("bchreg", 2, h160)
    s["p2pkh_legacy"] = base58check(bytes([0x6f]) + h160)
    s["p2pkh_script"] = script_of(p)
    s["p2sh"] = cli_json("decodescript", "51")["p2sh"]
    s["p2sh_script"] = script_of(s["p2sh"])
    # The covenant: a P2SH32 locking bytecode, as PHOTON's is.
    redeem = bytes.fromhex("5175")  # OP_TRUE OP_DROP, standing in for a covenant
    s["p2sh32"] = cashaddr("bchreg", 1, sha256d(redeem))
    s["p2sh32_token_aware"] = cashaddr("bchreg", 3, sha256d(redeem))
    s["p2sh32_script"] = "aa20" + sha256d(redeem).hex() + "87"
    assert script_of(s["p2sh32"]) == s["p2sh32_script"], script_of(s["p2sh32"])

    # Plain outputs: two to the same key hash, one to each script hash.
    for address, amount in ((p, "1.0"), (p, "0.7"), (s["p2sh"], "0.5"), (s["p2sh32"], "0.25")):
        cli("sendtoaddress", address, amount)

    # Two token categories, each made by spending a coinbase's output 0.
    coinbases = sorted((u for u in cli_json("listunspent", "100")
                        if u["vout"] == 0 and Decimal(u["amount"]) >= 25), key=lambda u: u["txid"])
    holder = cli("getnewaddress")
    holder_token_aware = cashaddr("bchreg", 2, cashaddr_hash(holder)[1])

    def genesis(coinbase: dict, outputs: dict) -> str:
        change = cli("getnewaddress")
        spent = sum(Decimal(str(v["amount"])) for v in outputs.values())
        outputs[change] = str(Decimal(coinbase["amount"]) - spent - Decimal("0.001"))
        for v in outputs.values():
            if isinstance(v, dict):
                v["amount"] = str(v["amount"])
        raw = cli("createrawtransaction", json.dumps([{"txid": coinbase["txid"], "vout": 0}]),
                  json.dumps(outputs))
        signed = cli_json("signrawtransactionwithwallet", raw)
        return cli("sendrawtransaction", signed["hex"])

    s["category_1"] = coinbases[0]["txid"]
    s["category_2"] = coinbases[1]["txid"]
    s["genesis_1"] = genesis(coinbases[0], {
        s["p2sh32_token_aware"]: {"amount": "0.0001", "tokenData": {
            "category": s["category_1"], "nft": {"capability": "mutable", "commitment": "beef"}}},
        s["p2pkh_token_aware"]: {"amount": "0.0001", "tokenData": {
            "category": s["category_1"], "amount": 1000}},
    })
    s["genesis_2"] = genesis(coinbases[1], {
        holder_token_aware: {"amount": "0.0001", "tokenData": {
            "category": s["category_2"], "nft": {"capability": "minting"}}},
        cashaddr("bchreg", 3, cashaddr_hash(s["p2sh"])[1]): {"amount": "0.0001", "tokenData": {
            "category": s["category_2"], "amount": 5, "nft": {"capability": "none", "commitment": "00"}}},
    })
    s["holder"] = holder
    cli("generatetoaddress", "1", mine)
    s["height"] = int(cli("getblockcount"))
    return s


def make_large_set(s: dict) -> dict:
    """BCHN only: FANOUT_TXS transactions of OUTPUTS_PER_TX outputs each, to the
    script hash of OP_TRUE, mined in as few blocks as they fit."""
    target = bytes.fromhex(s["p2sh_script"])
    coinbases = sorted((u for u in cli_json("listunspent", "100")
                        if u["vout"] == 0 and Decimal(u["amount"]) >= 25
                        and u["txid"] not in (s["category_1"], s["category_2"])),
                       key=lambda u: u["txid"])[:FANOUT_TXS]
    started = time.monotonic()
    sent = 0
    for coinbase in coinbases:
        total = int(Decimal(coinbase["amount"]) * 100_000_000)
        fee = 1_000_000
        each = (total - fee) // OUTPUTS_PER_TX
        tx = bytearray()
        tx += (2).to_bytes(4, "little")
        tx += compact(1) + bytes.fromhex(coinbase["txid"])[::-1] + (0).to_bytes(4, "little")
        tx += compact(0) + (0xffffffff).to_bytes(4, "little")
        tx += compact(OUTPUTS_PER_TX)
        out = each.to_bytes(8, "little") + compact(len(target)) + target
        tx += out * OUTPUTS_PER_TX
        tx += (0).to_bytes(4, "little")
        signed = cli_json("signrawtransactionwithwallet", tx.hex())
        cli("sendrawtransaction", signed["hex"])
        sent += 1
    blocks = 0
    while int(cli_json("getmempoolinfo")["size"]) > 0 and blocks < 10:
        cli("generatetoaddress", "1", s["mine"])
        blocks += 1
    return {"transactions": sent, "outputs": sent * OUTPUTS_PER_TX, "blocks": blocks,
            "seconds": round(time.monotonic() - started, 1), "height": int(cli("getblockcount"))}


# ---- comparing answers --------------------------------------------------------------

def unspent_view(u: dict) -> dict:
    """The fields BCHN 29.1.0 gives; 29.2.0 adds coinbase, blockhash and confirmations."""
    out = {k: u.get(k) for k in ("txid", "vout", "scriptPubKey", "amount", "height")}
    out["amount"] = Decimal(str(out["amount"])) if out["amount"] is not None else None
    if "tokenData" in u:
        out["tokenData"] = u["tokenData"]
    return out


def answer_view(r) -> dict | None:
    if not isinstance(r, dict):
        return None
    return {
        "success": r.get("success"),
        "unspents": sorted((unspent_view(u) for u in r.get("unspents", [])),
                           key=lambda u: (u["txid"], u["vout"])),
        "total_amount": Decimal(str(r.get("total_amount"))),
        "token_total_amounts": r.get("token_total_amounts"),
    }


def compare(label: str, objects: list, node_kth=KTH) -> dict:
    kr, ke, kms = timed_call(node_kth, "scantxoutset", "start", objects)
    br, be, bms = timed_call(BCHN, "scantxoutset", "start", objects)
    kv, bv = answer_view(kr), answer_view(br)
    row = {
        "label": label, "objects": objects,
        "same": ke is None and be is None and kv == bv,
        "unspents": len(kv["unspents"]) if kv else None,
        "bchn_unspents": len(bv["unspents"]) if bv else None,
        "total_amount": str(kv["total_amount"]) if kv else None,
        "bchn_total_amount": str(bv["total_amount"]) if bv else None,
        "token_total_amounts": kv["token_total_amounts"] if kv else None,
        "bchn_token_total_amounts": bv["token_total_amounts"] if bv else None,
        "searched_items": kr.get("searched_items") if isinstance(kr, dict) else None,
        "bchn_searched_items": br.get("searched_items") if isinstance(br, dict) else None,
        "kth_ms": kms, "bchn_ms": bms,
        "kth_error": ke, "bchn_error": be,
    }
    if not row["same"]:
        row["kth_answer"] = kr
        row["bchn_answer"] = br
    RESULTS["compare"].append(row)
    return row


def error_row(label: str, params: list, expect_same_text: bool) -> None:
    kr, ke = call(KTH, "scantxoutset", *params)
    br, be = call(BCHN, "scantxoutset", *params)
    row = {"label": label, "params": params, "kth": ke if ke else kr, "bchn": be if be else br,
           "same_code": (ke or {}).get("code") == (be or {}).get("code") and (ke is None) == (be is None),
           "same_result": ke is None and be is None and kr == br}
    if expect_same_text:
        row["same_text"] = (ke or {}).get("message") == (be or {}).get("message")
    RESULTS["errors"].append(row)


# ---- phases ---------------------------------------------------------------------------

def access(s: dict) -> None:
    me = own_address()
    RESULTS["setup"]["runner_address"] = me
    node_me = (me, KTH_PORT)

    # Off by default.
    start_kth("off", on=False, bind="127.0.0.1", callers=[])
    r, e = call(KTH, "scantxoutset", "status")
    RESULTS["access"].append({"case": "off (default): loopback status", "error": e,
                              "ok": bool(e) and e.get("code") == -32601 and "rpc.scantxoutset" in e.get("message", "")})
    stop_kth("off")

    # On, bound beyond loopback, no callers named.
    start_kth("wide", on=True, bind="0.0.0.0", callers=[])
    r, e = call(KTH, "scantxoutset", "status")
    RESULTS["access"].append({"case": "on, bound 0.0.0.0: loopback status", "result": r, "error": e,
                              "ok": e is None and r is None})
    r, e = call(node_me, "scantxoutset", "start", ["raw(zz)"])
    RESULTS["access"].append({"case": f"on, bound 0.0.0.0: caller {me} not named, malformed params",
                              "result": r, "error": e,
                              "ok": bool(e) and e.get("code") == -32601
                              and "rpc.scantxoutset_caller" in e.get("message", "")})
    r, e = call(node_me, "getblockcount")
    RESULTS["access"].append({"case": f"on, bound 0.0.0.0: caller {me}, another method", "result": r, "error": e,
                              "ok": e is None and isinstance(r, int)})
    text = stop_kth("wide")
    warnings = [line for line in text.splitlines() if "rpc.scantxoutset is on" in line]
    RESULTS["access"].append({"case": "on, bound 0.0.0.0: startup warning", "lines": warnings,
                              "ok": len(warnings) == 1 and "rpc.scantxoutset_caller (none)" in warnings[0]})

    # On, the runner's address named: the case of a client on another computer.
    start_kth("main", on=True, bind="0.0.0.0", callers=[me])
    got = wait_kth_height(s["height"], 600)
    RESULTS["setup"]["kth_synced_seconds"] = got
    r, e = call(node_me, "scantxoutset", "status")
    RESULTS["access"].append({"case": f"on, caller {me} named: status", "result": r, "error": e,
                              "ok": e is None and r is None})


def comparisons(s: dict) -> None:
    me = (RESULTS["setup"]["runner_address"], KTH_PORT)
    compare("raw: P2PKH script", [f"raw({s['p2pkh_script']})"])
    compare("raw: P2PKH script, upper case hex", [f"raw({s['p2pkh_script'].upper()})"])
    compare("addr: P2PKH cashaddr", [f"addr({s['p2pkh']})"], me)
    compare("addr: P2PKH cashaddr without prefix", [f"addr({s['p2pkh_bare']})"])
    compare("addr: P2PKH token-aware cashaddr", [f"addr({s['p2pkh_token_aware']})"])
    compare("addr: P2PKH legacy", [f"addr({s['p2pkh_legacy']})"])
    compare("addr: P2SH", [f"addr({s['p2sh']})"])
    compare("raw: P2SH", [f"raw({s['p2sh_script']})"])
    compare("addr: P2SH32", [f"addr({s['p2sh32']})"])
    compare("raw: covenant (P2SH32) with a mutable NFT, the PHOTON search", [f"raw({s['p2sh32_script']})"])
    compare("tok: category 1 (mutable NFT, fungible)", [f"tok({s['category_1']})"])
    compare("tok: category 2 (minting NFT, NFT with fungible)", [f"tok({s['category_2']})"])
    compare("several objects, object form with range", [
        f"raw({s['p2sh_script']})", f"tok({s['category_2']})",
        {"desc": f"addr({s['p2pkh']})", "range": 10}])
    compare("nothing found", ["raw(6a)"])

    # The PHOTON search, as Pickaxe reads it: the unspent with the category and a mutable NFT.
    r, e = call(KTH, "scantxoutset", "start", [f"raw({s['p2sh32_script']})"])
    found = [u for u in (r or {}).get("unspents", [])
             if u.get("tokenData", {}).get("category") == s["category_1"]
             and u.get("tokenData", {}).get("nft", {}).get("capability") == "mutable"]
    RESULTS["photon"] = {"found": found, "ok": len(found) == 1 and found[0]["tokenData"]["nft"]["commitment"] == "beef"}

    error_row("unknown action", ["bogus"], True)
    error_row("start without scan objects", ["start"], True)
    error_row("scan objects not an array", ["start", "raw(51)"], True)
    error_row("scan object neither string nor object", ["start", [1]], True)
    error_row("object without desc", ["start", [{"range": 1}]], True)
    error_row("range out of range", ["start", [{"desc": "raw(51)", "range": -1}]], True)
    error_row("invalid descriptor (kth names its position, not its text)", ["start", ["raw(51)", "raw(zz)"]], False)
    error_row("address of another network", ["start", ["addr(bitcoincash:qpzz8n7jp6847yyx8t33matrgcsdx6c0cvmtevrfgz)"]], False)
    error_row("pkh() (BCHN derives it; kth takes addr(), raw() and tok() only)",
              ["start", ["pkh(0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798)"]], False)
    error_row("status with no scan", ["status"], True)
    error_row("abort with no scan", ["abort"], True)


def status_and_abort(node, name: str, objects: list) -> dict:
    """Start a scan, read its status while it runs, abort it."""
    for attempt in range(6):
        box: dict = {}

        def scan():
            box["result"], box["error"], box["ms"] = timed_call(node, "scantxoutset", "start", objects)

        thread = threading.Thread(target=scan)
        thread.start()
        status = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and thread.is_alive():
            r, e = call(node, "scantxoutset", "status", timeout=10)
            if isinstance(r, dict):
                status = r
                break
            time.sleep(0.002)
        second = call(node, "scantxoutset", "start", objects, timeout=10) if status else None
        aborted = call(node, "scantxoutset", "abort", timeout=10) if status else None
        thread.join(600)
        if status:
            result = box.get("result") or {}
            row = {"node": name, "attempt": attempt + 1, "status": status,
                   "second_start": second[1] if second else None,
                   "abort": aborted[0] if aborted else None,
                   "start_success": result.get("success"), "start_searched": result.get("searched_items"),
                   "start_ms": box.get("ms")}
            row["ok"] = (row["abort"] is True and row["start_success"] is False
                         and (second[1] or {}).get("code") == -8)
            RESULTS["status_abort"].append(row)
            return row
    row = {"node": name, "ok": False, "note": "every scan ended before status saw it"}
    RESULTS["status_abort"].append(row)
    return row


def load(s: dict) -> None:
    rare = [f"raw({s['p2sh32_script']})"]
    made = make_large_set(s)
    RESULTS["load"]["set"] = made
    got = wait_kth_height(made["height"], 1800)
    RESULTS["load"]["kth_synced_seconds"] = got
    RESULTS["load"]["utxo_count_bchn"] = cli_json("gettxoutsetinfo").get("txouts")

    # One scan of the whole set on each node.
    kr, ke, kms = timed_call(KTH, "scantxoutset", "start", rare)
    br, be, bms = timed_call(BCHN, "scantxoutset", "start", rare)
    RESULTS["load"]["one_scan"] = {
        "kth_ms": kms, "bchn_ms": bms,
        "kth_searched": (kr or {}).get("searched_items"), "bchn_searched": (br or {}).get("searched_items"),
        "same": answer_view(kr) == answer_view(br), "kth_error": ke, "bchn_error": be}
    compare("tok: category 1, over the large set", [f"tok({s['category_1']})"])

    status_and_abort(KTH, "kth", rare)
    status_and_abort(BCHN, "bchn", rare)

    def measure(label: str, blocks: int) -> dict:
        lags, gbts = [], []
        for _ in range(blocks):
            # A transaction in each block, so a template has something to select.
            cli("sendtoaddress", s["holder"], "0.01")
            _, _, ms = timed_call(KTH, "getblocktemplate", timeout=60)
            gbts.append(ms)
            cli("generatetoaddress", "1", s["mine"])
            height = int(cli("getblockcount"))
            lag = wait_kth_height(height, 120)
            lags.append(round(lag * 1000, 1) if lag is not None else None)
            _, _, ms = timed_call(KTH, "getblocktemplate", timeout=60)
            gbts.append(ms)
            time.sleep(0.2)
        good = [x for x in lags if x is not None]
        return {"label": label, "blocks": blocks, "connect_ms": lags, "gbt_ms": gbts,
                "connect_ms_median": statistics.median(good) if good else None,
                "connect_ms_max": max(good) if good else None,
                "gbt_ms_median": statistics.median(gbts), "gbt_ms_max": max(gbts),
                "missed": sum(1 for x in lags if x is None)}

    RESULTS["load"]["baseline"] = measure("no scan running", 10)

    stop = threading.Event()
    scans: list = []

    def scanning():
        while not stop.is_set():
            r, e, ms = timed_call(KTH, "scantxoutset", "start", rare)
            scans.append({"ms": ms, "success": (r or {}).get("success"), "error": e,
                          "searched": (r or {}).get("searched_items")})

    worker = threading.Thread(target=scanning)
    worker.start()
    time.sleep(0.5)
    RESULTS["load"]["during_scans"] = measure("kth scanning back to back", 10)
    stop.set()
    worker.join(600)
    RESULTS["load"]["scans"] = {"count": len(scans), "ms": [x["ms"] for x in scans],
                                "all_success": all(x["success"] for x in scans),
                                "errors": [x["error"] for x in scans if x["error"]]}


def privacy(s: dict, log_text: str) -> None:
    # What only a scan names: these must be absent from the whole log.
    scanned = {
        "P2PKH script": s["p2pkh_script"], "P2SH script": s["p2sh_script"],
        "P2SH32 script": s["p2sh32_script"], "P2PKH cashaddr": s["p2pkh"].split(":")[1],
        "P2PKH legacy": s["p2pkh_legacy"], "P2SH32 cashaddr": s["p2sh32"].split(":")[1],
        "P2SH cashaddr": s["p2sh"].split(":")[1],
    }
    # Transaction ids the node also handles as transactions (the categories are
    # the ids of the coinbases their tokens were made from): any line that
    # names one is listed, and must not be about the RPC server.
    ids = {"category 1": s["category_1"], "category 2": s["category_2"],
           "genesis 1 txid": s["genesis_1"], "genesis 2 txid": s["genesis_2"]}
    lines = log_text.splitlines()
    for name, secret in scanned.items():
        RESULTS["privacy"].append({"secret": name, "lines": [l[:200] for l in lines if secret in l][:3],
                                   "ok": secret not in log_text})
    for name, secret in ids.items():
        hits = [l[:200] for l in lines if secret in l]
        RESULTS["privacy"].append({"secret": name, "lines": hits[:3],
                                   "ok": not any("rpc" in l.lower() or "scan" in l.lower() for l in hits)})
    RESULTS["privacy_log_bytes"] = len(log_text)
    RESULTS["privacy_counts_logged"] = [l[:300] for l in lines if "scantxoutset:" in l][-5:]


# ---- report ---------------------------------------------------------------------------

def report() -> str:
    out = ["# scantxoutset on kth against BCHN", ""]
    out.append(f"kth: `{RESULTS['versions'].get('kth')}`  ")
    out.append(f"BCHN: `{RESULTS['versions'].get('bchn')}`")
    out.append("")
    out.append("## Access")
    out.append("")
    out.append("| Case | OK | Answer |")
    out.append("|---|---|---|")
    for row in RESULTS["access"]:
        detail = row.get("error") or row.get("result") or row.get("lines")
        out.append(f"| {row['case']} | {'yes' if row['ok'] else '**no**'} | `{json.dumps(detail, default=str)[:220]}` |")
    out.append("")
    out.append("## Same scan objects to both nodes")
    out.append("")
    out.append("| Scan | Same answer | Unspents kth / BCHN | Total kth / BCHN | Token totals kth / BCHN | searched_items kth / BCHN | ms kth / BCHN |")
    out.append("|---|---|---|---|---|---|---|")
    for row in RESULTS["compare"]:
        out.append(f"| {row['label']} | {'yes' if row['same'] else '**no**'} | {row['unspents']} / {row['bchn_unspents']} "
                   f"| {row['total_amount']} / {row['bchn_total_amount']} "
                   f"| `{json.dumps(row['token_total_amounts'])}` / `{json.dumps(row['bchn_token_total_amounts'])}` "
                   f"| {row['searched_items']} / {row['bchn_searched_items']} | {row['kth_ms']} / {row['bchn_ms']} |")
    out.append("")
    photon = RESULTS.get("photon", {})
    out.append(f"PHOTON-style search (raw() of the covenant, the unspent with category 1 and a mutable NFT): "
               f"{'found' if photon.get('ok') else '**not found**'}")
    out.append("")
    out.append("## Errors")
    out.append("")
    out.append("| Request | kth | BCHN | Same code |")
    out.append("|---|---|---|---|")
    for row in RESULTS["errors"]:
        out.append(f"| {row['label']} | `{json.dumps(row['kth'], default=str)[:160]}` | "
                   f"`{json.dumps(row['bchn'], default=str)[:160]}` | {'yes' if row['same_code'] else 'no'} |")
    out.append("")
    out.append("## Status during a scan, and abort")
    out.append("")
    for row in RESULTS["status_abort"]:
        out.append(f"- {row['node']}: {'ok' if row['ok'] else '**not ok**'} `{json.dumps(row, default=str)[:400]}`")
    load_ = RESULTS["load"]
    if load_:
        out.append("")
        out.append("## Blocks and templates while kth scans a large set")
        out.append("")
        out.append(f"Set: `{json.dumps(load_.get('set'))}`; kth synced it in {load_.get('kth_synced_seconds')} s; "
                   f"BCHN txouts {load_.get('utxo_count_bchn')}.  ")
        out.append(f"One scan: `{json.dumps(load_.get('one_scan'), default=str)}`")
        out.append("")
        out.append("| | Blocks | Connect ms median | Connect ms max | Missed | getblocktemplate ms median | max |")
        out.append("|---|---|---|---|---|---|---|")
        for key in ("baseline", "during_scans"):
            m = load_.get(key)
            if m:
                out.append(f"| {m['label']} | {m['blocks']} | {m['connect_ms_median']} | {m['connect_ms_max']} | "
                           f"{m['missed']} | {m['gbt_ms_median']} | {m['gbt_ms_max']} |")
        out.append("")
        out.append(f"Scans run meanwhile: `{json.dumps(load_.get('scans'), default=str)[:600]}`")
    out.append("")
    out.append("## kth's debug log (verbose)")
    out.append("")
    out.append(f"{RESULTS.get('privacy_log_bytes')} bytes searched.")
    out.append("")
    for row in RESULTS["privacy"]:
        out.append(f"- {row['secret']}: {'ok' if row['ok'] else '**found**'}"
                   + (f", lines naming it: `{json.dumps(row['lines'])[:400]}`" if row["lines"] else ", in no line"))
    out.append(f"- what scantxoutset logs (counts, at debug level): `{json.dumps(RESULTS.get('privacy_counts_logged'))[:800]}`")
    for key in ("crashed",):
        if key in RESULTS["setup"]:
            out.append(f"\nCrashed: {RESULTS['setup'][key]}")
    return "\n".join(out) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    s: dict = {}
    log_text = ""
    try:
        start_bchn()
        RESULTS["versions"]["bchn"] = (call(BCHN, "getnetworkinfo")[0] or {}).get("subversion")
        s = make_outputs()
        RESULTS["setup"]["data"] = {k: v for k, v in s.items()}
        access(s)
        RESULTS["versions"]["kth"] = (call(KTH, "getnetworkinfo")[0] or {}).get("subversion")
        comparisons(s)
        load(s)
    except Exception as error:  # the report is still written
        RESULTS["setup"]["crashed"] = f"{type(error).__name__}: {error}"
    finally:
        log_text = stop_kth("main")
        if s:
            privacy(s, log_text)
        subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_DIR}", "-rpcuser=e2e",
                        "-rpcpassword=e2e", "-rpcport=18443", "stop"], capture_output=True)
        (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1, default=str))
        text = report()
        (OUT / "results.md").write_text(text)
        print(text)


if __name__ == "__main__":
    main()
