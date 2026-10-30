#!/usr/bin/env python3
"""Mine on a regtest kth through its own JSON-RPC and report what happens.

A probe, not a pass/fail test: it prints what kth answers to each mining call
a pool makes, so we can see how Knuth's light mining works today.

1. getblockchaininfo / getmininginfo: is the node up, is it caught up?
2. getblocktemplatelight: the fields kth gives a miner.
3. A coinbase-only block on that template (no merkle branch needed when kth
   selects no transactions), proof of work found, sent with submitblocklight.
4. getblockcount afterwards: did the chain grow?
5. getblockheader <tip> false: the raw-header form.

Standard library only (the package image has python3).
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

URL = "http://127.0.0.1:19332/"
AUTH = base64.b64encode(b"probe:probe").decode()


def call(method: str, *params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)})
    request = Request(URL, body.encode(), {"Authorization": f"Basic {AUTH}",
                                           "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=60) as response:
            reply = json.loads(response.read())
    except HTTPError as error:
        reply = json.loads(error.read() or b"{}")
    except OSError as error:
        return None, {"message": str(error)}
    return reply.get("result"), reply.get("error")


def show(label: str, method: str, *params):
    result, error = call(method, *params)
    print(f"\n== {label}: {method} {json.dumps(list(params))[:120]}")
    print(f"   result: {json.dumps(result)[:900]}")
    if error is not None:
        print(f"   error:  {json.dumps(error)[:900]}")
    return result, error


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


def coinbase(height: int, value: int) -> bytes:
    # BIP34 height, padded so the transaction is at least 100 bytes.
    script_sig = push(script_number(height)) + push(b"knuth-mining-probe".ljust(40, b"."))
    out_script = b"\x51"  # OP_TRUE
    return (struct.pack("<i", 2) + b"\x01" + b"\x00" * 32 + b"\xff\xff\xff\xff"
            + bytes([len(script_sig)]) + script_sig + b"\xff\xff\xff\xff"
            + b"\x01" + struct.pack("<q", value) + bytes([len(out_script)]) + out_script
            + struct.pack("<I", 0))


def main() -> None:
    for _ in range(120):
        result, error = call("getblockchaininfo")
        if error is None and result:
            break
        time.sleep(1)
    else:
        raise SystemExit("kth never answered getblockchaininfo")

    show("node", "getblockchaininfo")
    show("mining state", "getmininginfo")
    template, error = show("template", "getblocktemplatelight")
    if error is not None or not template:
        print("\nPROBE RESULT: kth gave no template")
        return
    print(f"   fields: {sorted(template)}")
    print(f"   merkle branch present: {'merkle' in template}")

    start, _ = call("getblockcount")
    base = coinbase(template["height"], template["coinbasevalue"])
    root = sha256d(base)  # coinbase-only block: the merkle root is its txid
    prev = bytes.fromhex(template["previousblockhash"])[::-1]
    bits = int(template["bits"], 16)
    target = (bits & 0xFFFFFF) << (8 * ((bits >> 24) - 3))
    when = max(template["curtime"], template.get("mintime", 0))
    for nonce in range(1 << 32):
        header = (struct.pack("<I", template["version"]) + prev + root
                  + struct.pack("<III", when, bits, nonce))
        if int.from_bytes(sha256d(header)[::-1], "big") <= target:
            break
    block_hash = sha256d(header)[::-1].hex()
    block = (header + b"\x01" + base).hex()
    print(f"\n   built block {block_hash} at height {template['height']} (nonce {nonce})")

    _, submit_error = show("submit", "submitblocklight", block, template["job_id"])
    time.sleep(2)
    end, _ = call("getblockcount")
    print(f"\n== chain height before {start}, after {end}")
    show("raw header of the tip", "getblockheader", call("getbestblockhash")[0], False)

    accepted = end is not None and start is not None and end > start
    print(f"\nPROBE RESULT: submitblocklight {'ACCEPTED the block' if accepted else 'did NOT add the block'}"
          + (f" (error: {submit_error})" if submit_error else ""))


if __name__ == "__main__":
    main()
