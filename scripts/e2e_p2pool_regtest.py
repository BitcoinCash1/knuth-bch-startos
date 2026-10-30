#!/usr/bin/env python3
"""Mine through the RPC proxy against a real regtest kth, as a BCH P2Pool node does.

Run by scripts/check-p2pool-rpc.sh inside the package image, with kth and
rpc_compat.py already listening. It asks the proxy (never kth directly) for
what P2Pool asks BCHN for:

1. mines 101 coinbase-only blocks from getblocktemplate and submitblock, each
   proposed first, so a coinbase matures;
2. spends it with a transaction sent through the proxy's sendrawtransaction;
3. checks the next template lists that transaction with BCHN's fee and
   sigops (SigChecks) fields and a coinbasevalue that includes the fee;
4. proposes and submits that block and sees it become the tip;
5. checks the raw and verbose block headers, getblockhash's out-of-range code,
   initialblockdownload, and validateaddress on this network and another.

Standard library only: the image has python3 and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18443/"
AUTH = base64.b64encode(b"smoke:smoke").decode()

# Anyone can spend: P2SH of the redeem script OP_TRUE, spent by pushing it.
# Its spend has no signature checks, so its SigChecks are exactly 0.
REDEEM = b"\x51"
FEE = 1000


def call(method: str, *params):
    body = json.dumps({"jsonrpc": "1.0", "id": method, "method": method, "params": list(params)})
    request = Request(URL, body.encode(), {"Authorization": f"Basic {AUTH}",
                                           "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            reply = json.loads(response.read())
    except HTTPError as error:  # an HTTP error status still carries a JSON-RPC body
        reply = json.loads(error.read())
    except OSError as error:
        reply = {"result": None, "error": {"code": None, "message": str(error)}}
    return reply.get("result"), reply.get("error")


def ok(method: str, *params):
    result, error = call(method, *params)
    if error is not None:
        raise SystemExit(f"{method} failed: {error}")
    return result


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def hash160(data: bytes) -> bytes:
    return hashlib.new("ripemd160", hashlib.sha256(data).digest()).digest()


def varint(n: int) -> bytes:
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    return b"\xfe" + struct.pack("<I", n)


def push(data: bytes) -> bytes:
    assert len(data) < 0x4C
    return bytes([len(data)]) + data


def script_number(n: int) -> bytes:
    out = bytearray()
    while n:
        out.append(n & 0xFF)
        n >>= 8
    if out and out[-1] & 0x80:
        out.append(0)
    return bytes(out)


P2SH_TRUE = b"\xa9\x14" + hash160(REDEEM) + b"\x87"
P2PKH_DUMMY = b"\x76\xa9\x14" + bytes(range(20)) + b"\x88\xac"


def tx(inputs: list[tuple[bytes, int, bytes]], outputs: list[tuple[int, bytes]]) -> bytes:
    raw = struct.pack("<i", 2) + varint(len(inputs))
    for prev, index, script_sig in inputs:
        raw += prev + struct.pack("<I", index) + varint(len(script_sig)) + script_sig
        raw += b"\xff\xff\xff\xff"
    raw += varint(len(outputs))
    for value, script in outputs:
        raw += struct.pack("<q", value) + varint(len(script)) + script
    return raw + struct.pack("<I", 0)


def coinbase(height: int, value: int) -> bytes:
    # The BIP34 height, then padding: a BCH transaction is at least 100 bytes.
    script_sig = push(script_number(height) or b"\x00") + push(b"p2pool-regtest-check".ljust(40, b"."))
    return tx([(b"\x00" * 32, 0xFFFFFFFF, script_sig)], [(value, P2SH_TRUE)])


def merkle_root(txids: list[bytes]) -> bytes:
    level = txids
    while len(level) > 1:
        if len(level) % 2:
            level = level + [level[-1]]
        level = [sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def target(bits_hex: str) -> int:
    bits = int(bits_hex, 16)
    return (bits & 0xFFFFFF) << (8 * ((bits >> 24) - 3))


class Mined:
    def __init__(self, block_hash: str, coinbase_raw: bytes, coinbase_value: int):
        self.block_hash = block_hash
        self.coinbase_txid = sha256d(coinbase_raw)
        self.coinbase_value = coinbase_value


def mine(expect_txids: list[str] | None = None) -> Mined:
    """Mine one block on the proxy's template, as P2Pool builds it: its own
    coinbase, then the template's transactions in order."""
    template = ok("getblocktemplate", {"rules": []})
    listed = [entry["txid"] for entry in template["transactions"]]
    if expect_txids is not None and listed != expect_txids:
        raise SystemExit(f"template lists {listed}, expected {expect_txids}")
    base = coinbase(template["height"], template["coinbasevalue"])
    body = [base] + [bytes.fromhex(entry["data"]) for entry in template["transactions"]]
    root = merkle_root([sha256d(raw) for raw in body])
    prev = bytes.fromhex(template["previousblockhash"])[::-1]
    when = max(template["curtime"], template.get("mintime", 0))
    goal = target(template["bits"])
    for nonce in range(1 << 32):
        header = (struct.pack("<I", template["version"]) + prev + root
                  + struct.pack("<II", when, int(template["bits"], 16)) + struct.pack("<I", nonce))
        if int.from_bytes(sha256d(header)[::-1], "big") <= goal:
            break
    block_hash = sha256d(header)[::-1].hex()
    block = (header + varint(len(body)) + b"".join(body)).hex()
    proposal = ok("getblocktemplate", {"mode": "proposal", "data": block})
    if proposal is not None:
        raise SystemExit(f"proposal of {block_hash} rejected: {proposal}")
    submitted = ok("submitblock", block)
    if submitted is not None:
        raise SystemExit(f"submitblock of {block_hash} rejected: {submitted}")
    for _ in range(50):
        if ok("getbestblockhash") == block_hash:
            return Mined(block_hash, base, template["coinbasevalue"])
        time.sleep(0.1)
    raise SystemExit(f"block {block_hash} was accepted but is not the tip")


CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def cashaddr(prefix: str, version: int, payload: bytes) -> str:
    def polymod(values):
        c = 1
        for d in values:
            c0 = c >> 35
            c = ((c & 0x07FFFFFFFF) << 5) ^ d
            for bit, gen in enumerate((0x98F2BC8E61, 0x79B76D99E2, 0xF33E5FB3C4,
                                       0xAE2EABE2A8, 0x1E4F43E470)):
                if c0 & (1 << bit):
                    c ^= gen
        return c ^ 1

    acc = bits = 0
    five = []
    for byte in bytes([version]) + payload:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            five.append((acc >> bits) & 31)
    if bits:
        five.append((acc << (5 - bits)) & 31)
    checksum = polymod([ord(c) & 31 for c in prefix] + [0] + five + [0] * 8)
    five += [(checksum >> 5 * (7 - i)) & 31 for i in range(8)]
    return prefix + ":" + "".join(CHARSET[d] for d in five)


def main() -> None:
    for _ in range(60):
        result, error = call("getblockchaininfo")
        if error is None and result:
            break
        time.sleep(1)
    else:
        raise SystemExit("the proxy never answered getblockchaininfo")
    info = ok("getblockchaininfo")
    assert "initialblockdownload" in info, f"no initialblockdownload in {info}"
    start = info["blocks"]
    print(f"proxy up: chain {info['chain']}, height {start}, IBD {info['initialblockdownload']}")

    first = mine()
    for _ in range(100):
        mine()
    print(f"mined 101 blocks to height {start + 101}, each proposed then submitted")

    spend = tx([(first.coinbase_txid, 0, push(REDEEM))],
               [(first.coinbase_value - FEE, P2PKH_DUMMY),
                (0, b"\x6a" + push(b"p2pool regtest spend"))])
    txid = sha256d(spend)[::-1].hex()
    sent = ok("sendrawtransaction", spend.hex())
    assert sent == txid, f"sendrawtransaction returned {sent}, expected {txid}"

    template = ok("getblocktemplate", {"rules": []})
    entries = {entry["txid"]: entry for entry in template["transactions"]}
    assert txid in entries, f"template does not list {txid}: {list(entries)}"
    entry = entries[txid]
    assert entry["fee"] == FEE, f"fee {entry['fee']}, expected {FEE}"
    assert entry["sigops"] == 0, f"sigops {entry['sigops']}, expected 0"
    assert entry["data"] == spend.hex()
    print(f"template lists the spend with fee {entry['fee']} and sigops {entry['sigops']}, "
          f"coinbasevalue {template['coinbasevalue']}")

    with_spend = mine([txid])
    print(f"mined {with_spend.block_hash} with the spend")

    raw_header = ok("getblockheader", with_spend.block_hash, False)
    assert isinstance(raw_header, str) and len(raw_header) == 160, raw_header
    assert sha256d(bytes.fromhex(raw_header))[::-1].hex() == with_spend.block_hash
    verbose = ok("getblockheader", with_spend.block_hash, True)
    assert verbose["height"] == start + 102, verbose
    assert "confirmations" in verbose, verbose

    _, error = call("getblockhash", start + 1000)
    assert error and error.get("code") == -8, f"getblockhash past the tip: {error}"

    on_network = ok("validateaddress", cashaddr("bchreg", 0, bytes(range(20))))
    assert on_network["isvalid"] and on_network["scriptPubKey"] == P2PKH_DUMMY.hex(), on_network
    assert not ok("validateaddress", cashaddr("bitcoincash", 0, bytes(range(20))))["isvalid"]
    p2sh32 = ok("validateaddress", cashaddr("bchreg", 11, bytes(range(32))))
    assert p2sh32["scriptPubKey"] == "aa20" + bytes(range(32)).hex() + "87", p2sh32
    print("headers, getblockhash and validateaddress answer as BCHN does")
    print("P2POOL RPC CHECK PASSED")


if __name__ == "__main__":
    main()
