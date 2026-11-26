#!/usr/bin/env python3
"""End-to-end: getblocktemplate proposals (BIP23), kth against BCHN 29.2.0.

BCHN regtest mines 1400 blocks to P2SH(OP_TRUE), so every coinbase is
spendable by anyone and the chain is past regtest's BIP65 height (1351).
kth syncs from it. Each proposal below is built here, on the tip both nodes
share, and the same hex is sent to BCHN and to kth with
getblocktemplate {"mode":"proposal","data":...}. The answers are compared one
by one. Two rows are expected to differ and are reported, not failed:

- bad-cb-height: BCHN 29.2.0's regtest never activates BIP34
  (BIP34Height = 100000000), kth's regtest enforces it from height 1
  (regtest_bip34_freeze = 0). A regtest parameter, not proposal mode.
- undersize: BCHN refuses a block holding a transaction under 65 bytes
  (bad-txns-undersize, ContextualCheckTransaction). kth checks the minimum
  size in transaction::accept, which the mempool uses and block validation
  does not, so kth's connect path takes such a block.

Then the valid proposal is mined (regtest proof of work, a few nonces) and
submitted to BCHN, kth must connect it, and both must answer "duplicate".
Nothing proposed may be stored: neither node may know a proposed block that
was not submitted.

Standard library only. Writes <out>/proposals.json and <out>/proposals.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BCHN = ("127.0.0.1", 18443)
BCHN_P2P = 18444
KTH = ("127.0.0.1", 19332)
KTH_P2P = 18555
AUTH = "e2e"
RPC_BASIC = base64.b64encode(b"e2e:e2e").decode()
CONTAINER = "kth-proposal"
BCHN_DIR = Path("/tmp/bchn-proposal")
DATA = Path("/tmp/kth-proposal-data")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-out").resolve()
CHAIN_LENGTH = 1400
# Rows whose answers differ for a reason outside proposal mode; see above.
EXPECTED_DIFFERENCES = {
    "bad-cb-height": "BCHN 29.2.0 regtest has no BIP34 (BIP34Height 100000000); kth regtest enforces it",
    "undersize": "kth's block validation does not apply the 65-byte minimum transaction size",
}
EVIDENCE: dict = {"ok": False, "error": "started", "rows": [], "heights": {}, "kth_argv": []}
_next_id = 0


def fail(message: str) -> NoReturn:
    EVIDENCE["ok"] = False
    EVIDENCE["error"] = message
    raise SystemExit(message)


def progress(message: str) -> None:
    print(message, flush=True)


# --- RPC ---------------------------------------------------------------------

def post(node, body: bytes, timeout: float = 120) -> dict:
    request = Request(f"http://{node[0]}:{node[1]}/", body,
                      {"Authorization": f"Basic {RPC_BASIC}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read() or b""
    except (URLError, OSError) as error:
        return {"status": None, "transport": f"{type(error).__name__}: {error}", "json": None, "raw": b""}
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return {"status": status, "json": parsed, "raw": raw}


def call(node, method: str, *params, timeout: float = 120):
    global _next_id
    _next_id += 1
    reply = post(node, json.dumps({"jsonrpc": "1.0", "id": _next_id, "method": method,
                                   "params": list(params)}).encode(), timeout)
    parsed = reply["json"]
    if isinstance(parsed, dict):
        return parsed.get("result"), parsed.get("error")
    return None, {"transport": reply.get("transport"), "status": reply.get("status"),
                  "raw": (reply.get("raw") or b"")[:200].decode("utf-8", "replace")}


def must(node, method: str, *params, timeout: float = 120):
    result, error = call(node, method, *params, timeout=timeout)
    if error is not None:
        fail(f"{'kth' if node == KTH else 'BCHN'} {method} failed: {error}")
    return result


def rpc_wait(node, name: str, seconds: int = 180) -> None:
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        result, error = call(node, "getblockcount", timeout=10)
        if error is None and result is not None:
            return
        last = error
        time.sleep(1)
    fail(f"{name} never answered getblockcount within {seconds}s: {last}")


def propose(node, data: str):
    """The node's answer to a proposal: its result, or its error as code: message."""
    result, error = call(node, "getblocktemplate", {"mode": "proposal", "data": data})
    if error is not None:
        if isinstance(error, dict) and "code" in error:
            return f"error {error['code']}: {error.get('message')}"
        return f"no answer: {error}"
    return result


# --- Serialization ------------------------------------------------------------

def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def varint(value: int) -> bytes:
    if value < 0xFD:
        return value.to_bytes(1, "little")
    if value <= 0xFFFF:
        return b"\xfd" + value.to_bytes(2, "little")
    return b"\xfe" + value.to_bytes(4, "little")


def push(data: bytes) -> bytes:
    assert len(data) < 0x4C
    return bytes([len(data)]) + data


def script_number(value: int) -> bytes:
    out = bytearray()
    while value:
        out.append(value & 0xFF)
        value >>= 8
    if out and out[-1] & 0x80:
        out.append(0)
    return bytes(out)


TRUE_SCRIPT = bytes([0x51])
# P2SH(OP_TRUE), read from BCHN (validateaddress) once it runs: hashlib's
# RIPEMD-160 is missing where OpenSSL 3 leaves out its legacy provider.
P2SH_TRUE = b""


def tx(version: int, inputs: list[tuple[bytes, int, bytes]], outputs: list[tuple[int, bytes]],
       locktime: int = 0) -> bytes:
    raw = version.to_bytes(4, "little") + varint(len(inputs))
    for prev, index, script_sig in inputs:
        raw += prev + index.to_bytes(4, "little") + varint(len(script_sig)) + script_sig
        raw += (0xFFFFFFFF).to_bytes(4, "little")
    raw += varint(len(outputs))
    for value, script in outputs:
        raw += value.to_bytes(8, "little") + varint(len(script)) + script
    return raw + locktime.to_bytes(4, "little")


def txid(raw: bytes) -> bytes:
    """Internal byte order."""
    return sha256d(raw)


def coinbase(height: int, value: int, height_in_script: int | None = None, small: bool = False) -> bytes:
    """`small`: no padding and an OP_TRUE output, 64 bytes, under the
    65-byte minimum transaction size."""
    script_sig = push(script_number(height if height_in_script is None else height_in_script))
    if small:
        return tx(1, [(b"\x00" * 32, 0xFFFFFFFF, script_sig)], [(value, TRUE_SCRIPT)])
    script_sig += push(b"kth bip23 proposal e2e")
    return tx(1, [(b"\x00" * 32, 0xFFFFFFFF, script_sig)], [(value, P2SH_TRUE)])


def spend(prev_txid_display: str, index: int, value: int, script_sig: bytes = push(TRUE_SCRIPT)) -> bytes:
    return tx(2, [(bytes.fromhex(prev_txid_display)[::-1], index, script_sig)], [(value, P2SH_TRUE)])


def canonical(txs: list[bytes]) -> list[bytes]:
    return sorted(txs, key=lambda raw: txid(raw)[::-1])


def merkle_root(hashes: list[bytes]) -> bytes:
    if not hashes:
        return b"\x00" * 32
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def block(prev_display: str, timestamp: int, bits: int, txs: list[bytes], version: int = 0x20000000,
          merkle: bytes | None = None, nonce: int = 0) -> bytes:
    root = merkle_root([txid(raw) for raw in txs]) if merkle is None else merkle
    header = (version.to_bytes(4, "little") + bytes.fromhex(prev_display)[::-1] + root
              + timestamp.to_bytes(4, "little") + bits.to_bytes(4, "little") + nonce.to_bytes(4, "little"))
    return header + varint(len(txs)) + b"".join(txs)


def block_hash(raw: bytes) -> str:
    return sha256d(raw[:80])[::-1].hex()


def mine(raw: bytes, bits: int) -> bytes:
    """Grind the nonce until the header meets `bits` (regtest: a few tries)."""
    exponent, mantissa = bits >> 24, bits & 0xFFFFFF
    target = mantissa << (8 * (exponent - 3))
    for nonce in range(1 << 20):
        candidate = raw[:76] + nonce.to_bytes(4, "little") + raw[80:]
        if int.from_bytes(sha256d(candidate[:80]), "little") <= target:
            return candidate
    fail("no nonce met the regtest target")


# --- Nodes --------------------------------------------------------------------

def start_bchn() -> None:
    if shutil.which("bitcoind") is None:
        fail("missing bitcoind on PATH (the workflow fetches BCHN 29.2.0)")
    BCHN_DIR.mkdir(parents=True, exist_ok=True)
    started = subprocess.run(
        ["bitcoind", "-regtest", f"-datadir={BCHN_DIR}", "-daemon", "-server",
         f"-rpcuser={AUTH}", f"-rpcpassword={AUTH}", f"-rpcport={BCHN[1]}", f"-port={BCHN_P2P}",
         "-listen=1", "-allowunconnectedmining", "-disablewallet=1"],
        capture_output=True, text=True, timeout=30)
    if started.returncode != 0:
        fail(f"bitcoind failed to start: {(started.stderr or started.stdout).strip()}")
    rpc_wait(BCHN, "BCHN")


def start_kth(image: str, src: Path, binary: str) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(DATA / "e2e", ignore_errors=True)
    relative = Path(binary).resolve().relative_to(src.resolve())
    workdir = "/src/" + relative.parent.as_posix()
    argv = [
        "--init_run", "--network", "regtest", "--node.display", "daemon",
        "--db.directory", "/data/e2e", "--db.db_mode", "full",
        "--net.inbound_port", str(KTH_P2P), "--net.inbound_connections", "0",
        "--net.outbound_connections", "1", "--net.peer", f"127.0.0.1:{BCHN_P2P}",
        "--rpc.enabled", "true", "--rpc.bind", "127.0.0.1", "--rpc.port", str(KTH[1]),
        "--rpc.user", AUTH, "--rpc.password", AUTH,
        "--log.debug_file", "/data/kth-debug.log", "--log.error_file", "/data/kth-error.log",
    ]
    EVIDENCE["kth_argv"] = argv
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=30)
    result = subprocess.run(
        ["docker", "run", "--detach", "--name", CONTAINER, "--network", "host", "--platform", "linux/amd64",
         "-v", f"{src.resolve()}:/src", "-v", f"{DATA}:/data", "-w", workdir,
         "--entrypoint", "./" + relative.name, image, *argv],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        fail(f"docker run of kth failed: {(result.stderr or result.stdout).strip()}")
    rpc_wait(KTH, "kth")


def wait_same_tip(seconds: int = 600) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        bchn_tip, _ = call(BCHN, "getbestblockhash")
        kth_tip, _ = call(KTH, "getbestblockhash")
        if bchn_tip and bchn_tip == kth_tip:
            return bchn_tip
        time.sleep(1)
    fail(f"kth did not reach BCHN's tip within {seconds}s")


def cleanup() -> None:
    try:
        logs = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True, timeout=30)
        (OUT / "kth-docker.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        pass
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=60)
    if shutil.which("bitcoin-cli"):
        subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_DIR}", f"-rpcuser={AUTH}",
                        f"-rpcpassword={AUTH}", f"-rpcport={BCHN[1]}", "stop"], capture_output=True, timeout=30)
    for name in ("kth-debug.log", "kth-error.log"):
        if (DATA / name).is_file():
            shutil.copy(DATA / name, OUT / name)
    if (BCHN_DIR / "regtest" / "debug.log").is_file():
        shutil.copy(BCHN_DIR / "regtest" / "debug.log", OUT / "bchn-debug.log")


# --- The comparison -------------------------------------------------------------

def run() -> None:
    image = os.environ.get("KTH_IMAGE", "").strip()
    src = Path(os.environ.get("KTH_SRC", "").strip() or ".")
    binary = os.environ.get("KTH_BIN", "").strip()
    if not image or not binary:
        fail("KTH_IMAGE and KTH_BIN must be set")
    OUT.mkdir(parents=True, exist_ok=True)

    start_bchn()
    global P2SH_TRUE
    address = must(BCHN, "decodescript", TRUE_SCRIPT.hex())["p2sh"]
    P2SH_TRUE = bytes.fromhex(must(BCHN, "validateaddress", address)["scriptPubKey"])
    progress(f"mining {CHAIN_LENGTH} blocks to P2SH(OP_TRUE) {address}")
    for _ in range(CHAIN_LENGTH // 200):
        must(BCHN, "generatetoaddress", 200, address, timeout=600)
    start_kth(image, src, binary)
    tip = wait_same_tip()
    height = must(BCHN, "getblockcount")
    EVIDENCE["heights"]["tip"] = height
    progress(f"kth and BCHN share tip {tip} at height {height}")

    header = must(BCHN, "getblockheader", tip)
    bits = int(header["bits"], 16)
    mtp = header["mediantime"]
    parent = header["previousblockhash"]
    now = int(time.time())
    new_height = height + 1
    subsidy = (50 * 100_000_000) >> (new_height // 150)
    fee = 1000

    def coinbase_output(h: int) -> tuple[str, int]:
        block_at = must(BCHN, "getblock", must(BCHN, "getblockhash", h), 2)
        out = block_at["tx"][0]
        return out["txid"], round(out["vout"][0]["value"] * 100_000_000)

    first_txid, first_value = coinbase_output(1)
    second_txid, second_value = coinbase_output(2)
    third_txid, third_value = coinbase_output(3)
    immature_txid, immature_value = coinbase_output(height)

    valid_spend = spend(first_txid, 0, first_value - fee)
    valid_txs = [coinbase(new_height, subsidy + fee), valid_spend]
    valid = block(tip, now, bits, valid_txs)

    bad_script = spend(second_txid, 0, second_value - fee, push(b"\x00"))
    double_a = spend(first_txid, 0, first_value - fee)
    double_b = spend(first_txid, 0, first_value - 2 * fee)
    missing = spend("11" * 32, 0, 1000)
    pair = canonical([spend(first_txid, 0, first_value - fee), spend(third_txid, 0, third_value - fee)])
    # [coinbase, x, y, y] in canonical order otherwise: the tree pairs y with
    # itself, and has the root of [coinbase, x, y] (CVE-2012-2459).
    repeated_pair = [coinbase(new_height, subsidy + fee), pair[0], pair[1], pair[1]]

    cases = [
        ("valid", valid.hex()),
        ("wrong parent", block(parent, now, bits, [coinbase(height, subsidy + fee), valid_spend]).hex()),
        ("inflated coinbase", block(tip, now, bits, [coinbase(new_height, subsidy + fee + 1), valid_spend]).hex()),
        ("bad merkle root", block(tip, now, bits, valid_txs, merkle=b"\x00" * 32).hex()),
        ("bad bits", block(tip, now, bits - 1, valid_txs).hex()),
        ("invalid script", block(tip, now, bits, [coinbase(new_height, subsidy + fee), bad_script]).hex()),
        ("double spend", block(tip, now, bits,
                               [coinbase(new_height, subsidy + 2 * fee)] + canonical([double_a, double_b])).hex()),
        ("duplicate (the tip)", must(BCHN, "getblock", tip, 0)),
        ("duplicate (height 700)", must(BCHN, "getblock", must(BCHN, "getblockhash", 700), 0)),
        ("missing input", block(tip, now, bits, [coinbase(new_height, subsidy), missing]).hex()),
        ("immature coinbase spend", block(tip, now, bits, [coinbase(new_height, subsidy + fee),
                                                           spend(immature_txid, 0, immature_value - fee)]).hex()),
        ("spends more than its input", block(tip, now, bits, [coinbase(new_height, subsidy),
                                                              spend(first_txid, 0, first_value + 1)]).hex()),
        ("non-canonical order", block(tip, now, bits, [coinbase(new_height, subsidy + 2 * fee)]
                                      + list(reversed(pair))).hex()),
        ("repeated last pair", block(tip, now, bits, repeated_pair).hex()),
        ("time-too-old", block(tip, mtp, bits, valid_txs).hex()),
        ("time-too-new", block(tip, now + 3 * 3600, bits, valid_txs).hex()),
        ("old version", block(tip, now, bits, valid_txs, version=1).hex()),
        ("bad-cb-height", block(tip, now, bits, [coinbase(new_height, subsidy + fee, height_in_script=height),
                                                 valid_spend]).hex()),
        ("undersize", block(tip, now, bits, [coinbase(new_height, subsidy, small=True)]).hex()),
        ("does not decode", "00"),
    ]

    rows = []
    for name, data in cases:
        before = (call(BCHN, "getbestblockhash")[0], call(KTH, "getbestblockhash")[0])
        if before[0] != before[1]:
            fail(f"the nodes are on different tips before '{name}': {before}")
        bchn = propose(BCHN, data)
        kth = propose(KTH, data)
        match = bchn == kth
        row = {"case": name, "bchn": bchn, "kth": kth, "match": match}
        if not match and name in EXPECTED_DIFFERENCES:
            row["expected_difference"] = EXPECTED_DIFFERENCES[name]
        rows.append(row)
        progress(f"{name:28} BCHN {json.dumps(bchn):45} kth {json.dumps(kth):45} {'match' if match else 'DIFFER'}")
    EVIDENCE["rows"] = rows

    # Nothing proposed was stored: neither node knows a proposed block.
    stored = []
    for name, data in cases:
        if name.startswith("duplicate") or len(data) < 160:
            continue
        hash_display = block_hash(bytes.fromhex(data))
        for node, label in ((BCHN, "BCHN"), (KTH, "kth")):
            result, error = call(node, "getblockheader", hash_display)
            if error is None:
                stored.append(f"{label} holds {name} {hash_display}")
    EVIDENCE["stored_after_proposals"] = stored

    # The valid proposal, mined and submitted to BCHN, is a block both connect.
    mined = mine(valid, bits)
    submitted, error = call(BCHN, "submitblock", mined.hex())
    EVIDENCE["submitted"] = {"hash": block_hash(mined), "bchn_submitblock": submitted, "error": error}
    after = wait_same_tip(120)
    EVIDENCE["submitted"]["shared_tip"] = after
    EVIDENCE["submitted"]["duplicate_answers"] = {
        "bchn": propose(BCHN, mined.hex()), "kth": propose(KTH, mined.hex())}

    unexpected = [row["case"] for row in rows if not row["match"] and "expected_difference" not in row]
    lines = ["| Proposal | BCHN 29.2.0 | kth | |", "|---|---|---|---|"]
    for row in rows:
        verdict = "same" if row["match"] else ("known difference" if "expected_difference" in row else "DIFFERENT")
        lines.append(f"| {row['case']} | `{json.dumps(row['bchn'])}` | `{json.dumps(row['kth'])}` | {verdict} |")
    (OUT / "proposals.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)

    problems = []
    if unexpected:
        problems.append(f"answers differ: {unexpected}")
    if stored:
        problems.append(f"proposed blocks were stored: {stored}")
    if submitted is not None or after != block_hash(mined):
        problems.append(f"the valid proposal, mined, was not connected by both: {EVIDENCE['submitted']}")
    duplicates = EVIDENCE["submitted"]["duplicate_answers"]
    if duplicates["bchn"] != "duplicate" or duplicates["kth"] != "duplicate":
        problems.append(f"the connected block is not a duplicate on both: {duplicates}")
    if problems:
        fail("; ".join(problems))
    EVIDENCE["ok"] = True
    EVIDENCE["error"] = None


def main() -> None:
    try:
        run()
    finally:
        try:
            cleanup()
        finally:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "proposals.json").write_text(json.dumps(EVIDENCE, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
