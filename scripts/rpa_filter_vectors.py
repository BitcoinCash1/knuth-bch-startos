#!/usr/bin/env python3
"""TEST ONLY. Test vectors for filter type 2, the input prefix filter for
reusable payment addresses, made with the reference in knuth_rpc_e2e.py (the
spec alone, no node's code). Prints them as JSON; with --markdown, as the
vectors document other implementations are checked against.

Standard library only.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import knuth_rpc_e2e as ref  # noqa: E402

sha256d = ref.sha256d
compact_size = ref.compact_size
FINAL = 0xffffffff


def tx_bytes(version: int, inputs: list[tuple[bytes, int, bytes, int]], outputs: list[tuple[int, bytes]]) -> bytes:
    out = struct.pack("<I", version) + compact_size(len(inputs))
    for txid, index, script, sequence in inputs:
        out += ref.rpa_input_bytes(txid, index, script, sequence)
    out += compact_size(len(outputs))
    for value, script in outputs:
        out += struct.pack("<Q", value) + compact_size(len(script)) + script
    return out + struct.pack("<I", 0)


def coinbase(height: int) -> bytes:
    script = ref.push(ref.script_number(height) if height else b"\x00") + ref.push(b"rpa")
    return tx_bytes(1, [(bytes(32), 0xffffffff, script, FINAL)], [(50 * 100_000_000, b"\x51")])


def merkle(txids: list[bytes]) -> bytes:
    level = list(txids)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def block_bytes(height: int, previous: bytes, transactions: list[bytes]) -> bytes:
    header = (struct.pack("<I", 0x20000000) + previous + merkle([sha256d(t) for t in transactions])
              + struct.pack("<III", 1_700_000_000 + 600 * height, 0x207fffff, 0))
    return header + compact_size(len(transactions)) + b"".join(transactions)


def spend(inputs: list[tuple[bytes, int, bytes, int]]) -> bytes:
    return tx_bytes(2, inputs, [(1000, b"\x51")])


def block_vectors() -> list[dict]:
    blocks: list[dict] = []
    previous = bytes(32)

    def add(name: str, what: str, body: list[bytes]) -> None:
        nonlocal previous
        height = len(blocks)
        raw = block_bytes(height, previous, [coinbase(height)] + body)
        transactions = ref.rpa_read_block(raw)
        values = ref.rpa_values_of([inputs for _, inputs in transactions[1:]])
        blocks.append({"name": name, "what": what, "block": raw, "values": values,
                       "inputs": [inputs for _, inputs in transactions]})
        previous = sha256d(raw[:80])

    add("empty", "a coinbase alone: no values", [])

    add("one input", "one transaction after the coinbase with one input",
        [spend([(b"\x11" * 32, 0, b"\x51", FINAL)])])

    # Input 1's sequence is ground until its value equals input 0's.
    first = (b"\x22" * 32, 0, b"\x51", FINAL)
    wanted = ref.rpa_value(ref.rpa_input_bytes(*first))
    sequence = next(s for s in range(1 << 32)
                    if ref.rpa_value(ref.rpa_input_bytes(b"\x22" * 32, 1, b"\x51", s)) == wanted)
    add("two inputs sharing a prefix", f"one transaction with two inputs whose values are equal "
        f"(input 1's sequence, {sequence}, ground to it): one value",
        [spend([first, (b"\x22" * 32, 1, b"\x51", sequence)])])

    add("several transactions", "two transactions after the coinbase, with 2 and 3 inputs: five values",
        [spend([(b"\x33" * 32, i, b"", FINAL) for i in range(2)]),
         spend([(b"\x44" * 32, i, ref.push(bytes([i]) * 20), 0xfffffffe - i) for i in range(3)])])

    # A transaction of 31 inputs; the 31st one's value is not among the first
    # 30's, so leaving it out changes the filter.
    fill = 0x55
    while True:
        thirty_one = [(bytes([fill]) * 32, i, b"", FINAL) for i in range(31)]
        values = {ref.rpa_value(ref.rpa_input_bytes(*i)) for i in thirty_one[:30]}
        if ref.rpa_value(ref.rpa_input_bytes(*thirty_one[30])) not in values:
            break
        fill += 1
    add("31 inputs", f"one transaction with 31 inputs (outpoints {fill:02x}..{fill:02x}:0 to :30, empty "
        "scripts): the values of the first 30, and not the 31st", [spend(thirty_one)])
    return blocks


def large_block() -> dict:
    """1,000 transactions of 30 inputs each: transaction j (1 to 1,000) spends
    outpoints whose txid (stored order) is j as four little-endian bytes and
    28 zero bytes, indexes 0 to 29, with empty scripts and sequence
    0xffffffff. Enough distinct values for k = 0."""
    transactions = []
    for j in range(1, 1001):
        txid = struct.pack("<I", j) + bytes(28)
        transactions.append([ref.rpa_input_bytes(txid, i, b"", FINAL) for i in range(30)])
    values = ref.rpa_values_of(transactions)
    return {"values": values, "inputs": 30_000}


def set_last_bit(data: bytes) -> bytes:
    return data[:-1] + bytes([data[-1] | 1])


def malformed() -> list[dict]:
    pack = ref.rpa_pack_bits
    bits = ref.rpa_rice_bits
    cases = [
        ("empty", b"", "truncated: no count"),
        ("count only", bytes([1]), "truncated: no k"),
        ("no stream", bytes([1, 15]), "truncated: no value"),
        ("k above 15", bytes([1, 16]) + pack(bits(16, [0])), "k above 15"),
        ("gap of 0", bytes([2, 14]) + pack(bits(14, [5, 0])), "a gap of 0 after the first value"),
        ("value above 65535", bytes([1, 15]) + pack(bits(15, [65536])), "a value above 65,535"),
        ("sum above 65535", bytes([2, 14]) + pack(bits(14, [65535, 1])), "a value above 65,535"),
        ("fewer values", bytes([2, 14]) + pack(bits(14, [5])), "fewer values than N"),
        ("more values", bytes([1, 15]) + pack(bits(15, [7]) + bits(15, [9])), "more values than N"),
        ("padding", bytes([2, 14]) + set_last_bit(pack(bits(14, [0, 1]))), "a padding bit set"),
        ("trailing after none", bytes([0, 0]), "trailing bytes"),
        ("trailing after one", ref.rpa_encode([7]) + b"\x00", "trailing bytes"),
        ("long count", bytes.fromhex("fd0100") + bytes([15]) + pack(bits(15, [7])),
         "N not in its shortest CompactSize form"),
        ("count above 65536", bytes.fromhex("fe01000100") + bytes([0]) + pack(bits(0, [0])),
         "N above 65,536"),
        # The same set written with another k than the one N gives: a second
        # encoding of one set, refused so each set has one filter and hash.
        ("k below the one N gives", bytes([1, 0]) + pack(bits(0, [3])), "k not the one N gives"),
        ("k one below the one N gives", bytes([1, 14]) + pack(bits(14, [3])), "k not the one N gives"),
    ]
    out = []
    for name, data, why in cases:
        try:
            ref.rpa_decode(data)
        except ValueError as error:
            out.append({"name": name, "hex": data.hex(), "refused": why, "reference": str(error)})
            continue
        raise SystemExit(f"the reference took the malformed case {name}")
    return out


def accepted() -> list[dict]:
    pack = ref.rpa_pack_bits
    even = list(range(0, 65536, 2))
    every = list(range(65536))
    cases = [
        ("value 0", [0], ref.rpa_encode([0])),
        ("value 65535", [65535], ref.rpa_encode([65535])),
        ("two values", [1, 2], ref.rpa_encode([1, 2])),
        ("even values, k = 0", even, ref.rpa_encode(even)),
        ("every value", every, ref.rpa_encode(every)),
    ]
    out = []
    for name, values, data in cases:
        assert ref.rpa_decode(data) == values, name
        out.append({"name": name, "count": len(values), "hex": data.hex(),
                    "canonical": data == ref.rpa_encode(values), "hash": sha256d(data).hex()})
    return out


def main() -> None:
    blocks = block_vectors()
    header = bytes(32)
    chain = []
    for b in blocks:
        served = ref.rpa_encode(b["values"])
        assert ref.rpa_decode(served) == b["values"]
        filter_hash = sha256d(served)
        header = sha256d(filter_hash + header)
        chain.append({"name": b["name"], "what": b["what"], "block": b["block"].hex(),
                      "block_hash": sha256d(b["block"][:80]).hex(),
                      "values": [f"{v:04x}" for v in b["values"]],
                      "k": ref.rpa_rice_parameter(len(b["values"])) if b["values"] else None,
                      "filter": served.hex(), "filter_hash": filter_hash.hex(), "header": header.hex(),
                      "input_values": [[f"{ref.rpa_value(i):04x}" for i in inputs] for inputs in b["inputs"][1:]]})
    large = large_block()
    served = ref.rpa_encode(large["values"])
    out = {
        "blocks": chain,
        "large": {"inputs": large["inputs"], "count": len(large["values"]),
                  "k": ref.rpa_rice_parameter(len(large["values"])), "bytes": len(served),
                  "head": served[:16].hex(), "tail": served[-8:].hex(), "filter_hash": sha256d(served).hex(),
                  "first_values": [f"{v:04x}" for v in large["values"][:4]],
                  "last_values": [f"{v:04x}" for v in large["values"][-4:]]},
        "accepted": accepted(),
        "malformed": malformed(),
    }
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
