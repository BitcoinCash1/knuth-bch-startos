#!/usr/bin/env python3
"""Bitcoin-RPC compatibility sidecar for Knuth v1.4.0 and v1.5.0.

kth documents getblock/getrawtransaction, but fetch_block() and
fetch_transaction() are stubs after the LMDB→blk*.dat move (block_chain.cpp:
"LMDB block storage removed - blocks now in flat files"; still so in v1.5.0,
where getrawtransaction answers "not found" even for mempool transactions).
It also has no getnetworkinfo, getblocktemplate, submitblock or
validateaddress — which Fulcrum, BCH Explorer, ASICSeer, EloPool and P2Pool
need.

This process sits in front of kth, serves those methods from the flat block
files + light-GBT, and proxies everything else unchanged.

Mining: kth's getblocktemplatelight names its transaction selection only by
job_id, which kth derives from the content (double SHA-256 over the selected
txids). The full template is built only when the proxy can name that exact
selection and knows every selected transaction's bytes, fee and SigChecks;
otherwise getblocktemplate answers an error instead of a template whose
blocks kth could not take. kth 1.5.0 takes no transactions from peers and
returns no transaction bytes, so the bytes come from the transactions this
proxy relayed with sendrawtransaction (kept in a file beside the block
files) and from the block files.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import socket
import socketserver
from pathlib import Path
from datetime import datetime
from typing import Any, Iterable, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ZERO_HASH = "00" * 32
CASHADDR_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CASHADDR_GEN = [0x98F2BC8E61, 0x79B76D99E2, 0xF33E5FB3C4, 0xAE2EABE2A8, 0x1E4F43E470]
# CashAddr hash sizes by the low three bits of the version byte.
CASHADDR_HASH_SIZES = (20, 24, 28, 32, 40, 48, 56, 64)
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
# Network (as normalize_chain names it) -> CashAddr prefix and the legacy
# base58 version bytes of P2PKH and P2SH, as BCHN's chainparams set them.
CASHADDR_PREFIX = {
    "main": "bitcoincash",
    "test": "bchtest",
    "test4": "bchtest",
    "scale": "bchtest",
    "chip": "bchtest",
    "regtest": "bchreg",
}
LEGACY_VERSIONS = {"main": (0x00, 0x05)}
TESTNET_LEGACY_VERSIONS = (0x6F, 0xC4)


def _cashaddr_polymod(values: list[int]) -> int:
    c = 1
    for d in values:
        c0 = c >> 35
        c = ((c & 0x07FFFFFFFF) << 5) ^ d
        for i in range(5):
            if (c0 >> i) & 1:
                c ^= CASHADDR_GEN[i]
    return c


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool = True) -> list[int] | None:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def decode_cashaddr(addr: str, default_prefix: str) -> tuple[str, int, bytes] | None:
    """Return (prefix, version byte, hash) of a CashAddr, or None if invalid.

    Follows BCHN's cashaddr::Decode and DecodeCashAddrContent: the prefix may
    be left out (the network's is then used), upper or lower case but not
    mixed, the checksum must verify, the version byte's reserved bit must be
    clear and the hash must have the size its version byte names.
    """
    if not addr or (addr != addr.lower() and addr != addr.upper()):
        return None
    lower = addr.lower()
    if lower.count(":") > 1:
        return None
    if ":" in lower:
        prefix, payload = lower.split(":", 1)
        if not prefix or any(not "a" <= c <= "z" for c in prefix):
            return None
    else:
        prefix, payload = default_prefix, lower
    if len(payload) < 8 + 1 or any(c not in CASHADDR_CHARSET for c in payload):
        return None
    data = [CASHADDR_CHARSET.index(c) for c in payload]
    # Encoders XOR the checksum with 1, so a valid address leaves exactly 1.
    if _cashaddr_polymod([ord(c) & 31 for c in prefix] + [0] + data) != 1:
        return None
    content = _convertbits(data[:-8], 5, 8, pad=False)
    if not content:
        return None
    version, hash_bytes = content[0], bytes(content[1:])
    if version & 0x80 or len(hash_bytes) != CASHADDR_HASH_SIZES[version & 0x07]:
        return None
    return prefix, version, hash_bytes


def encode_cashaddr(prefix: str, version: int, hash_bytes: bytes) -> str:
    """The lowercase CashAddr of `hash_bytes` with `version` under `prefix`."""
    data = _convertbits([version, *hash_bytes], 8, 5, pad=True) or []
    checksum = _cashaddr_polymod([ord(c) & 31 for c in prefix] + [0] + data + [0] * 8) ^ 1
    data += [(checksum >> (5 * (7 - i))) & 31 for i in range(8)]
    return prefix + ":" + "".join(CASHADDR_CHARSET[d] for d in data)


def decode_base58check(text: str) -> bytes | None:
    """The payload of a base58check string, or None if it does not verify."""
    text = text.strip(" \t\n\r\f\v")
    if not text or any(c not in BASE58_ALPHABET for c in text):
        return None
    number = 0
    for c in text:
        number = number * 58 + BASE58_ALPHABET.index(c)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    raw = b"\x00" * (len(text) - len(text.lstrip("1"))) + body
    if len(raw) < 4 or sha256d(raw[:-4])[:4] != raw[-4:]:
        return None
    return raw[:-4]


def decode_destination(addr: str, chain: str) -> tuple[bool, bytes, bool] | None:
    """(pays a script, hash, token-aware) of an address on `chain`, as BCHN's
    DecodeDestination reads it: CashAddr with the network's prefix first, then
    a legacy base58 address with the network's version bytes. None when it is
    not a valid address for the network."""
    prefix = CASHADDR_PREFIX[chain]
    parsed = decode_cashaddr(addr, prefix)
    if parsed is not None:
        got_prefix, version, hash_bytes = parsed
        if got_prefix != prefix:
            return None
        kind = version >> 3
        if kind in (0, 2) and len(hash_bytes) == 20:
            return False, hash_bytes, kind == 2
        if kind in (1, 3) and len(hash_bytes) in (20, 32):
            return True, hash_bytes, kind == 3
        return None
    legacy = decode_base58check(addr)
    if legacy is None or len(legacy) != 21:
        return None
    pubkey_version, script_version = LEGACY_VERSIONS.get(chain, TESTNET_LEGACY_VERSIONS)
    if legacy[0] == pubkey_version:
        return False, legacy[1:], False
    if legacy[0] == script_version:
        return True, legacy[1:], False
    return None


# Knuth writes blk*.dat from height 1; genesis is compiled in. Hex is the
# standard Bitcoin genesis coinbase with each network's header.
# Chipnet verified via Flowee getblock(..., false).
GENESIS_HEX = {
    "000000001dd410c49a788668ce26751718cc797474d3152a5fc073dd44fd9f7b":
        "0100000000000000000000000000000000000000000000000000000000000000000000003ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4af1a93c5fffff001d01d3cd060101000000010000000000000000000000000000000000000000000000000000000000000000ffffffff4d04ffff001d0104455468652054696d65732030332f4a616e2f32303039204368616e63656c6c6f72206f6e206272696e6b206f66207365636f6e64206261696c6f757420666f722062616e6b73ffffffff0100f2052a01000000434104678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5fac00000000",
    # BCH mainnet shares the BTC genesis block.
    "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f": (
        "010000000000000000000000000000000000000000000000000000000000000000000000"
        "3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a29ab5f49"
        "ffff001d1dac2b7c01010000000100000000000000000000000000000000000000000000"
        "0000000000000000000000ffffffff4d04ffff001d0104455468652054696d6573203033"
        "2f4a616e2f32303039204368616e63656c6c6f72206f6e206272696e6b206f6620736563"
        "6f6e64206261696c6f757420666f722062616e6b73ffffffff0100f2052a010000004341"
        "04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc"
        "3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5fac00000000"
    ),
}
CHAIN_ALIAS = {
    "mainnet": "main",
    "main": "main",
    "testnet": "test",
    "testnet3": "test",
    "test": "test",
    "testnet4": "test4",
    "test4": "test4",
    "scalenet": "scale",
    "scale": "scale",
    "chipnet": "chip",
    "chip": "chip",
    "regtest": "regtest",
}


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def hex_rev(b: bytes) -> str:
    return b[::-1].hex()


def read_compact_size(buf: bytes, i: int) -> tuple[int, int]:
    if i >= len(buf):
        raise ValueError("truncated compact size")
    n = buf[i]
    if n < 253:
        return n, i + 1
    if n == 253:
        return struct.unpack_from("<H", buf, i + 1)[0], i + 3
    if n == 254:
        return struct.unpack_from("<I", buf, i + 1)[0], i + 5
    return struct.unpack_from("<Q", buf, i + 1)[0], i + 9


def write_compact_size(n: int) -> bytes:
    if n < 253:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def iter_tx_slices(block: bytes) -> list[bytes]:
    if len(block) < 80:
        raise ValueError("block shorter than header")
    i = 80
    count, i = read_compact_size(block, i)
    out: list[bytes] = []
    for _ in range(count):
        start = i
        i += 4  # version
        nin, i = read_compact_size(block, i)
        for _ in range(nin):
            i += 36
            slen, i = read_compact_size(block, i)
            i += slen + 4
        nout, i = read_compact_size(block, i)
        for _ in range(nout):
            i += 8
            slen, i = read_compact_size(block, i)
            i += slen
        i += 4  # locktime
        out.append(block[start:i])
    return out


def bits_to_target(bits: int) -> int:
    exp = bits >> 24
    mant = bits & 0xFFFFFF
    return mant * (1 << (8 * (exp - 3))) if exp > 3 else mant >> (8 * (3 - exp))


def bits_to_difficulty(bits: int) -> float:
    target = bits_to_target(bits)
    if target <= 0:
        return 0.0
    # Bitcoin difficulty-1 target
    return 0xFFFF * (1 << (8 * (0x1D - 3))) / float(target)


# ---- transactions, blocks and scripts ---------------------------------------

LOCKTIME_THRESHOLD = 500_000_000
SEQUENCE_FINAL = 0xFFFFFFFF
MAX_MONEY = 21_000_000 * 100_000_000
# BCHN consensus: MIN_TX_SIZE_UPGRADE9, MAX_COINBASE_SCRIPTSIG_SIZE,
# MAX_FUTURE_BLOCK_TIME and the transaction versions allowed since 2023.
MIN_TX_SIZE = 65
MAX_COINBASE_SCRIPTSIG_SIZE = 100
MAX_FUTURE_BLOCK_TIME = 2 * 60 * 60
TX_VERSIONS = (1, 2)

OP_PUSHDATA1, OP_PUSHDATA2, OP_PUSHDATA4 = 0x4C, 0x4D, 0x4E
OP_1NEGATE, OP_1, OP_16 = 0x4F, 0x51, 0x60
OP_IF, OP_NOTIF, OP_BEGIN, OP_UNTIL, OP_ELSE, OP_ENDIF = 0x63, 0x64, 0x65, 0x66, 0x67, 0x68
OP_DEFINE, OP_INVOKE = 0x89, 0x8A
OP_CHECKSIG, OP_CHECKSIGVERIFY = 0xAC, 0xAD
OP_CHECKMULTISIG, OP_CHECKMULTISIGVERIFY = 0xAE, 0xAF
OP_CHECKDATASIG, OP_CHECKDATASIGVERIFY = 0xBA, 0xBB
SIG_CHECK_OPS = frozenset({
    OP_CHECKSIG, OP_CHECKSIGVERIFY, OP_CHECKMULTISIG, OP_CHECKMULTISIGVERIFY,
    OP_CHECKDATASIG, OP_CHECKDATASIGVERIFY,
})
FLOW_OPS = frozenset({OP_IF, OP_NOTIF, OP_BEGIN, OP_UNTIL, OP_ELSE, OP_ENDIF, OP_DEFINE, OP_INVOKE})
PREFIX_TOKEN = 0xEF


class TxIn(NamedTuple):
    prev_hash: bytes  # internal byte order
    prev_index: int
    script_sig: bytes
    sequence: int


class TxOut(NamedTuple):
    value: int
    script: bytes  # the whole scriptPubKey field, token prefix included


class Tx(NamedTuple):
    version: int
    inputs: list[TxIn]
    outputs: list[TxOut]
    locktime: int

    def is_coinbase(self) -> bool:
        return (
            len(self.inputs) == 1
            and self.inputs[0].prev_hash == b"\x00" * 32
            and self.inputs[0].prev_index == 0xFFFFFFFF
        )


def parse_transaction_at(buf: bytes, start: int) -> tuple[Tx, int]:
    """Decode the transaction at `start`; return it and where it ends."""
    try:
        version = struct.unpack_from("<i", buf, start)[0]
        i = start + 4
        count, i = read_compact_size(buf, i)
        if count > len(buf):
            raise ValueError("input count past the end")
        inputs: list[TxIn] = []
        for _ in range(count):
            prev_hash = buf[i : i + 32]
            prev_index = struct.unpack_from("<I", buf, i + 32)[0]
            size, i = read_compact_size(buf, i + 36)
            script_sig = buf[i : i + size]
            i += size
            sequence = struct.unpack_from("<I", buf, i)[0]
            i += 4
            if len(script_sig) != size:
                raise ValueError("truncated input script")
            inputs.append(TxIn(prev_hash, prev_index, script_sig, sequence))
        count, i = read_compact_size(buf, i)
        if count > len(buf):
            raise ValueError("output count past the end")
        outputs: list[TxOut] = []
        for _ in range(count):
            value = struct.unpack_from("<q", buf, i)[0]
            size, i = read_compact_size(buf, i + 8)
            script = buf[i : i + size]
            i += size
            if len(script) != size:
                raise ValueError("truncated output script")
            outputs.append(TxOut(value, script))
        locktime = struct.unpack_from("<I", buf, i)[0]
    except struct.error as e:
        raise ValueError(f"truncated transaction: {e}") from e
    return Tx(version, inputs, outputs, locktime), i + 4


def parse_transaction(raw: bytes) -> Tx:
    tx, end = parse_transaction_at(raw, 0)
    if end != len(raw):
        raise ValueError("trailing bytes after the transaction")
    return tx


def txid_of(raw: bytes) -> str:
    return hex_rev(sha256d(raw))


def decode_block(raw: bytes) -> tuple[bytes, list[bytes], list[Tx]] | None:
    """(header, serialized transactions, decoded transactions), or None when
    the bytes do not start with a block. Trailing bytes are ignored, as BCHN's
    DecodeHexBlk ignores them."""
    if len(raw) < 81:
        return None
    try:
        count, i = read_compact_size(raw, 80)
        if count > len(raw):
            return None
        slices: list[bytes] = []
        txs: list[Tx] = []
        for _ in range(count):
            tx, end = parse_transaction_at(raw, i)
            slices.append(raw[i:end])
            txs.append(tx)
            i = end
    except ValueError:
        return None
    return raw[:80], slices, txs


def merkle_root(txids: list[bytes]) -> bytes:
    """Merkle root of transaction ids in internal byte order."""
    if not txids:
        return b"\x00" * 32
    layer = list(txids)
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [sha256d(layer[k] + layer[k + 1]) for k in range(0, len(layer), 2)]
    return layer[0]


def selection_job_id(txids: Iterable[str]) -> str:
    """kth's job_id for a template selection: double SHA-256 over the
    selected txids in internal byte order, as hex (job_store::add)."""
    return sha256d(b"".join(bytes.fromhex(txid)[::-1] for txid in txids)).hex()


EMPTY_SELECTION_JOB_ID = selection_job_id([])


def is_final(tx: Tx, height: int, cutoff_time: int) -> bool:
    """BCHN's IsFinalTx: final at `height` with BIP113's median-time cutoff."""
    if tx.locktime == 0:
        return True
    limit = height if tx.locktime < LOCKTIME_THRESHOLD else cutoff_time
    if tx.locktime < limit:
        return True
    return all(txin.sequence == SEQUENCE_FINAL for txin in tx.inputs)


def script_number(n: int) -> bytes:
    """Minimal little-endian sign-magnitude encoding of a script number."""
    if n == 0:
        return b""
    out = bytearray()
    magnitude = abs(n)
    while magnitude:
        out.append(magnitude & 0xFF)
        magnitude >>= 8
    if out[-1] & 0x80:
        out.append(0x80 if n < 0 else 0)
    elif n < 0:
        out[-1] |= 0x80
    return bytes(out)


def coinbase_height_prefix(height: int) -> bytes:
    """`CScript() << height`, the start BIP34 requires of a coinbase script."""
    if height == 0:
        return b"\x00"
    if 1 <= height <= 16:
        return bytes([OP_1 - 1 + height])
    data = script_number(height)
    return bytes([len(data)]) + data


def parse_script(script: bytes) -> list[tuple[int, bytes | None]] | None:
    """(opcode, pushed data or None) for each operation, or None when a push
    runs past the end."""
    ops: list[tuple[int, bytes | None]] = []
    i, n = 0, len(script)
    while i < n:
        op = script[i]
        i += 1
        if op > OP_PUSHDATA4:
            ops.append((op, None))
            continue
        if op < OP_PUSHDATA1:
            size = op
        else:
            width = {OP_PUSHDATA1: 1, OP_PUSHDATA2: 2, OP_PUSHDATA4: 4}[op]
            if i + width > n:
                return None
            size = int.from_bytes(script[i : i + width], "little")
            i += width
        if i + size > n:
            return None
        ops.append((op, script[i : i + size]))
        i += size
    return ops


def push_only_stack(script: bytes) -> list[bytes] | None:
    """The stack a push-only script leaves, or None if it is not push-only."""
    ops = parse_script(script)
    if ops is None:
        return None
    stack: list[bytes] = []
    for op, data in ops:
        if data is not None:
            stack.append(data)
        elif op == OP_1NEGATE:
            stack.append(b"\x81")
        elif OP_1 <= op <= OP_16:
            stack.append(bytes([op - OP_1 + 1]))
        else:
            return None
    return stack


def _small_number(op: int, data: bytes | None) -> int | None:
    if data is None:
        return op - OP_1 + 1 if OP_1 <= op <= OP_16 else None
    if not data:
        return 0
    if len(data) == 1 and data[0] < 0x80:
        return data[0]
    return None


def is_p2sh(locking: bytes) -> bool:
    """P2SH20 (OP_HASH160 <20> OP_EQUAL) or P2SH32 (OP_HASH256 <32> OP_EQUAL)."""
    return (
        len(locking) == 23 and locking[:2] == b"\xa9\x14" and locking[-1] == 0x87
    ) or (len(locking) == 35 and locking[:2] == b"\xaa\x20" and locking[-1] == 0x87)


def locking_bytecode(script_field: bytes) -> bytes | None:
    """The locking bytecode of an output, without its CashTokens prefix."""
    if not script_field or script_field[0] != PREFIX_TOKEN:
        return script_field
    try:
        i = 1 + 32
        bitfield = script_field[i]
        i += 1
        if bitfield & 0x40:  # has a commitment
            size, i = read_compact_size(script_field, i)
            i += size
        if bitfield & 0x10:  # has an amount
            _, i = read_compact_size(script_field, i)
    except (IndexError, ValueError, struct.error):
        return None
    return script_field[i:] if i <= len(script_field) else None


def script_sigchecks(script: bytes, stack: list[bytes]) -> int | None:
    """SigChecks of the last script an input runs, started on `stack`, in a
    transaction its node accepted. None when they cannot be read without
    running the script.

    The counts follow BCHN's interpreter: a signature check counts once when
    its signature is not empty; legacy CHECKMULTISIG counts its key count when
    any signature is not empty, Schnorr CHECKMULTISIG its signature count. In
    a valid spend every *VERIFY check and a final CHECKSIG succeed, so their
    signatures are not empty.
    """
    ops = parse_script(script)
    if ops is None:
        return None
    opcodes = [op for op, data in ops if data is None]
    if not any(op in SIG_CHECK_OPS or op == OP_INVOKE for op in opcodes):
        return 0
    # <m> <key>... <n> OP_CHECKMULTISIG
    if len(ops) >= 3 and ops[-1] == (OP_CHECKMULTISIG, None):
        required = _small_number(*ops[0])
        count = _small_number(*ops[-2])
        keys = ops[1:-2]
        if (
            required is not None
            and count == len(keys)
            and 0 <= required <= count
            and all(data is not None for _, data in keys)
        ):
            if len(stack) != required + 1:
                return None
            dummy, signatures = stack[0], stack[1:]
            if dummy:
                return required
            return count if any(signatures) else 0
    if any(op in FLOW_OPS or op in (OP_CHECKMULTISIG, OP_CHECKMULTISIGVERIFY) for op in opcodes):
        return None
    checks = 0
    for position, (op, data) in enumerate(ops):
        if data is not None:
            continue
        if op in (OP_CHECKSIGVERIFY, OP_CHECKDATASIGVERIFY):
            checks += 1
        elif op in (OP_CHECKSIG, OP_CHECKDATASIG):
            if position != len(ops) - 1:
                return None
            checks += 1
    return checks


def input_sigchecks(script_sig: bytes, locking: bytes) -> int | None:
    """SigChecks of an input spending `locking`. Unlocking bytecode is
    push-only by consensus, so only the locking or redeem script checks."""
    stack = push_only_stack(script_sig)
    if stack is None:
        return None
    if is_p2sh(locking):
        if not stack:
            return None
        return script_sigchecks(stack[-1], stack[:-1])
    return script_sigchecks(locking, stack)


def header_from_fields(fields: dict[str, Any]) -> bytes | None:
    """The 80-byte header serialized from kth's verbose getblockheader."""
    try:
        prev = bytes.fromhex(fields["previousblockhash"])[::-1]
        merkle = bytes.fromhex(fields["merkleroot"])[::-1]
        if len(prev) != 32 or len(merkle) != 32:
            return None
        return (
            struct.pack("<I", int(fields["version"]) & 0xFFFFFFFF)
            + prev
            + merkle
            + struct.pack(
                "<III", int(fields["time"]), int(fields["bits"], 16), int(fields["nonce"])
            )
        )
    except (KeyError, TypeError, ValueError, AttributeError, struct.error):
        return None


class BlockIndex:
    """Incremental blk*.dat index.

    A full rescan is ~10s over 2.3G. Doing that on every file-size change
    (each new chipnet block) made getblock time out. We only read *new*
    bytes and never block lookups of hashes we already know.
    """

    def __init__(self, blocks_dir: Path):
        self.blocks_dir = blocks_dir
        self.cache_path = blocks_dir.parent / "rpc-compat-index.pkl"
        self.by_hash: dict[str, tuple[str, int, int] | bytes] = {}
        self.by_height: dict[int, str] = {}
        self.height_of_hash: dict[str, int] = {}
        self.prev_of: dict[str, str] = {}
        self.children: dict[str, list[str]] = {}
        self.txindex: dict[str, tuple[str, int]] = {}
        self.tip_hash = ZERO_HASH
        self.tip_height = -1
        self._offsets: dict[str, int] = {}
        self._lock = threading.RLock()
        self._ingest_lock = threading.Lock()

    def start_watcher(self) -> None:
        def loop() -> None:
            while True:
                time.sleep(2)
                try:
                    self.ingest_new()
                except Exception as e:
                    print(f"[rpc-compat] ingest error: {e}", flush=True)

        threading.Thread(target=loop, daemon=True, name="blk-ingest").start()

    def _inject_genesis(self) -> None:
        for gh, hx in GENESIS_HEX.items():
            raw = bytes.fromhex(hx)
            self.by_hash.setdefault(gh, raw)
            self.prev_of.setdefault(gh, ZERO_HASH)
            self.children.setdefault(ZERO_HASH, [])
            if gh not in self.children[ZERO_HASH]:
                self.children[ZERO_HASH].append(gh)
            try:
                txs = iter_tx_slices(raw)
            except ValueError:
                txs = []
            for ntx, txraw in enumerate(txs):
                self.txindex.setdefault(hex_rev(sha256d(txraw)), (gh, ntx))

    def _recompute_heights(self) -> None:
        referenced = set(self.prev_of.values())
        tips = [h for h in self.by_hash if h not in referenced]
        def walk_back(start: str) -> list[str]:
            chain: list[str] = []
            h = start
            seen: set[str] = set()
            while h in self.by_hash and h not in seen:
                chain.append(h)
                seen.add(h)
                h = self.prev_of.get(h, ZERO_HASH)
            return chain
        best: list[str] = []
        for tip in tips:
            chain = walk_back(tip)
            if len(chain) > len(best):
                best = chain
        self.by_height = {}
        self.height_of_hash = {}
        if best:
            start_height = 0 if self.prev_of.get(best[-1], ZERO_HASH) == ZERO_HASH else 1
            for i, h in enumerate(reversed(best)):
                ht = start_height + i
                self.by_height[ht] = h
                self.height_of_hash[h] = ht
            self.tip_hash = best[0]
            self.tip_height = start_height + len(best) - 1
        else:
            self.tip_hash, self.tip_height = ZERO_HASH, -1

    def _extend_heights(self) -> int:
        added = 0
        while True:
            kids = self.children.get(self.tip_hash, [])
            if not kids:
                break
            nxt = kids[0]
            if nxt in self.height_of_hash:
                # already on a side branch; stop
                if self.height_of_hash[nxt] <= self.tip_height:
                    break
            self.tip_height += 1
            self.by_height[self.tip_height] = nxt
            self.height_of_hash[nxt] = self.tip_height
            self.tip_hash = nxt
            added += 1
        return added

    def _scan_bytes(self, path: Path, blob: bytes, base: int) -> tuple[list, int]:
        found: list[tuple] = []
        i = 0
        n = len(blob)
        consumed = base
        while i + 8 <= n:
            magic = blob[i : i + 4]
            if magic == b"\x00\x00\x00\x00":
                break
            size = struct.unpack_from("<I", blob, i + 4)[0]
            start = i + 8
            end = start + size
            if size < 80 or end > n:
                break
            block = blob[start:end]
            hdr = block[:80]
            bh = hex_rev(sha256d(hdr))
            prev = hex_rev(hdr[4:36])
            try:
                txs = iter_tx_slices(block)
            except ValueError:
                txs = []
            txids = [hex_rev(sha256d(raw)) for raw in txs]
            found.append((bh, prev, str(path), base + start, size, txids))
            consumed = base + end
            i = end
        return found, consumed

    def ingest_new(self) -> int:
        """Read only unread tail of blk*.dat. Returns number of new blocks."""
        if not self._ingest_lock.acquire(blocking=False):
            return 0
        try:
            files = sorted(self.blocks_dir.glob("blk*.dat"))
            with self._lock:
                offsets = dict(self._offsets)
            # Shrink / rotate → full rebuild
            for path in files:
                key = str(path)
                size = path.stat().st_size
                if offsets.get(key, 0) > size:
                    print("[rpc-compat] blk file shrank — full reindex", flush=True)
                    self.rebuild()
                    return 0
            new_found: list[tuple] = []
            new_offsets = dict(offsets)
            for path in files:
                key = str(path)
                size = path.stat().st_size
                start_at = new_offsets.get(key, 0)
                if start_at >= size:
                    continue
                with path.open("rb") as f:
                    f.seek(start_at)
                    blob = f.read()
                found, consumed = self._scan_bytes(path, blob, start_at)
                new_found.extend(found)
                new_offsets[key] = consumed
            if not new_found:
                with self._lock:
                    self._offsets = new_offsets
                return 0
            with self._lock:
                for bh, prev, pstr, start, size, txids in new_found:
                    self.by_hash[bh] = (pstr, start, size)
                    self.prev_of[bh] = prev
                    self.children.setdefault(prev, []).append(bh)
                    for ntx, txid in enumerate(txids):
                        self.txindex[txid] = (bh, ntx)
                added = self._extend_heights()
                self._offsets = new_offsets
            if added:
                print(
                    f"[rpc-compat] +{len(new_found)} blocks (heights +{added}) "
                    f"tip={self.tip_height}",
                    flush=True,
                )
            return len(new_found)
        finally:
            self._ingest_lock.release()

    def rebuild(self) -> None:
        t0 = time.time()
        with self._lock:
            self.by_hash.clear()
            self.by_height.clear()
            self.height_of_hash.clear()
            self.prev_of.clear()
            self.children.clear()
            self.txindex.clear()
            self._offsets.clear()
            self.tip_hash = ZERO_HASH
            self.tip_height = -1
            self._inject_genesis()
        files = sorted(self.blocks_dir.glob("blk*.dat"))
        for path in files:
            data = path.read_bytes()
            found, consumed = self._scan_bytes(path, data, 0)
            with self._lock:
                for bh, prev, pstr, start, size, txids in found:
                    self.by_hash[bh] = (pstr, start, size)
                    self.prev_of[bh] = prev
                    self.children.setdefault(prev, []).append(bh)
                    for ntx, txid in enumerate(txids):
                        self.txindex[txid] = (bh, ntx)
                self._offsets[str(path)] = consumed
        with self._lock:
            self._recompute_heights()
            n = len(self.by_hash)
            txs = len(self.txindex)
            tip_h, tip = self.tip_height, self.tip_hash
        print(
            f"[rpc-compat] indexed {n} blocks, {txs} txs, "
            f"tip={tip_h} {tip[:16]}… in {time.time() - t0:.1f}s",
            flush=True,
        )

    def maybe_refresh(self) -> None:
        self.ingest_new()

    def read_block(self, blockhash: str) -> bytes | None:
        key = blockhash.lower()
        with self._lock:
            rec = self.by_hash.get(key)
        if rec is None:
            self.ingest_new()
            with self._lock:
                rec = self.by_hash.get(key)
        if rec is None:
            return None
        if isinstance(rec, bytes):
            return rec
        path_s, start, size = rec
        with open(path_s, "rb") as f:
            f.seek(start)
            return f.read(size)

    def height_of(self, blockhash: str) -> int | None:
        with self._lock:
            return self.height_of_hash.get(blockhash.lower())

    def hash_at(self, height: int) -> str | None:
        with self._lock:
            h = self.by_height.get(height)
        if h is None and height > self.tip_height:
            self.ingest_new()
            with self._lock:
                h = self.by_height.get(height)
        return h


class TxStore:
    """Transactions this proxy relayed to Knuth with sendrawtransaction.

    kth 1.5.0 neither returns transaction bytes (getrawtransaction is a stub)
    nor takes transactions from peers, so its mempool holds only what was sent
    to it, and through this proxy that is everything sent over the public RPC
    port. The bytes are kept, one hex transaction per line, in a file beside
    the block files, so that a restart (when kth reloads its mempool.dat) does
    not leave the proxy unable to describe transactions kth will mine.
    """

    # A transaction no longer in kth's mempool is forgotten after this long,
    # so one sent during a template build is not dropped before kth lists it.
    RETAIN_SECONDS = 3600

    def __init__(self, path: Path | None = None):
        self.path = path
        self._txs: dict[str, tuple[bytes, float]] = {}
        self._lock = threading.Lock()
        if path is not None:
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return
        now = time.time()
        for line in lines:
            try:
                raw = bytes.fromhex(line.strip())
                parse_transaction(raw)
            except ValueError:
                continue
            self._txs[txid_of(raw)] = (raw, now)

    def add(self, raw: bytes) -> str:
        txid = txid_of(raw)
        with self._lock:
            known = txid in self._txs
            self._txs[txid] = (raw, time.time())
            if not known and self.path is not None:
                try:
                    with self.path.open("a") as out:
                        out.write(raw.hex() + "\n")
                except OSError as e:
                    print(f"[rpc-compat] cannot record transaction {txid}: {e}", flush=True)
        return txid

    def get(self, txid: str) -> bytes | None:
        with self._lock:
            entry = self._txs.get(txid)
        return entry[0] if entry else None

    def retain(self, mempool: set[str], now: float | None = None) -> None:
        """Forget transactions out of kth's mempool for RETAIN_SECONDS."""
        now = time.time() if now is None else now
        with self._lock:
            stale = [
                txid for txid, (_, seen) in self._txs.items()
                if txid not in mempool and now - seen > self.RETAIN_SECONDS
            ]
            for txid in mempool & self._txs.keys():
                self._txs[txid] = (self._txs[txid][0], now)
            if not stale:
                return
            for txid in stale:
                del self._txs[txid]
            if self.path is None:
                return
            temp = self.path.with_suffix(".tmp")
            try:
                temp.write_text("".join(raw.hex() + "\n" for raw, _ in self._txs.values()))
                os.replace(temp, self.path)
            except OSError as e:
                print(f"[rpc-compat] cannot rewrite {self.path}: {e}", flush=True)


class Knuth:
    def __init__(self, url: str, user: str, password: str, timeout: float = 20.0):
        self.url = url
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.timeout = timeout

    def call(self, method: str, params: list[Any] | None = None) -> dict[str, Any]:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        ).encode()
        req = Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.auth,
            },
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            raw = e.read().decode() if e.fp else ""
            try:
                return json.loads(raw) if raw else {"error": {"code": e.code, "message": str(e)}}
            except json.JSONDecodeError:
                return {"error": {"code": e.code, "message": raw or str(e)}}
        except URLError as e:
            return {"error": {"code": -342, "message": f"backend unreachable: {e.reason}"}}


def normalize_chain(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return CHAIN_ALIAS.get(value.strip().lower(), value)


def node_version(binary: str) -> str | None:
    """Read the installed node's version, without inferring it from this proxy."""
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Knuth\s+v(\d+\.\d+\.\d+)", result.stdout)
    return match[1] if match else None


def current_peer_count(log_path: Path, now: float | None = None) -> int | None:
    """Read a recent status record, never turn an absent/stale record into peers."""
    now = time.time() if now is None else now
    try:
        with log_path.open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 2_000_000))
            lines = log.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if "[node] Knuth v" in line or "[p2p_node] maintain_outbound_connections started" in line:
            return None
        peers = re.search(r"\[p2p_node:status\]\s+Peers:\s+(\d+)\s*/\s*\d+", line)
        if not peers:
            continue
        stamp = re.search(r"\[(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?)\]", line)
        if not stamp:
            return None
        try:
            age = now - datetime.fromisoformat(stamp[1]).timestamp()
        except ValueError:
            return None
        # The node emits this status every ten seconds. Refuse old records
        # from a stopped or previous process even if other log lines are fresh.
        return int(peers[1]) if -5 <= age <= 120 else None
    return None


def parse_verbosity(raw: Any) -> int:
    if raw is None or raw is True:
        return 1
    if raw is False:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def decode_hash_param(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    h = value.strip().lower()
    if h.startswith("0x"):
        h = h[2:]
    if len(h) != 64:
        return None
    try:
        bytes.fromhex(h)
    except ValueError:
        return None
    return h


class MempoolEntry(NamedTuple):
    fee: int
    time: int
    depends: tuple[str, ...]


class TemplateUnavailable(Exception):
    """kth's template cannot be described as a full getblocktemplate."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# How often getblocktemplate re-reads kth's light template when the mempool
# moved between reading it and reading the mempool.
TEMPLATE_ATTEMPTS = 3


class Compat:
    def __init__(
        self, backend: Knuth, index: BlockIndex,
        debug_log: Path = Path("/data/debug.log"), version: str | None = None,
        txs: TxStore | None = None,
    ):
        self.backend = backend
        self.index = index
        self.debug_log = debug_log
        self.version = version
        self.started = time.time()
        self.txs = txs if txs is not None else TxStore()
        self._chain: str | None = None
        self._cache_lock = threading.Lock()
        # Immutable per transaction, so kept while the transaction is pooled.
        self._entries: dict[str, MempoolEntry] = {}
        self._sigchecks: dict[str, int] = {}

    def handle(self, method: str, params: list[Any]) -> dict[str, Any]:
        fn = getattr(self, f"rpc_{method}", None)
        if fn is None:
            return self.backend.call(method, params)
        try:
            return fn(params)
        except Exception as e:
            return {"result": None, "error": {"code": -32603, "message": str(e)}, "id": 1}

    def _ok(self, result: Any) -> dict[str, Any]:
        return {"result": result, "error": None, "id": 1}

    def _err(self, code: int, message: str) -> dict[str, Any]:
        return {"result": None, "error": {"code": code, "message": message}, "id": 1}

    def rpc_getblockchaininfo(self, params: list[Any]) -> dict[str, Any]:
        out = self.backend.call("getblockchaininfo", params)
        res = out.get("result")
        if isinstance(res, dict):
            if res.get("chain") is not None:
                res["chain"] = normalize_chain(res["chain"])
            # kth's getblockchaininfo has no initialblockdownload. Its
            # getmininginfo says whether the connected chain has reached its
            # headers (caughtup) and whether that tip is recent (fresh, within
            # node.notify_limit_hours, 24 by default like BCHN's tip age); kth
            # serves mining work only when both hold. Without them the field
            # is left out rather than guessed.
            mining = self.backend.call("getmininginfo").get("result")
            if isinstance(mining, dict):
                caught_up, fresh = mining.get("caughtup"), mining.get("fresh")
                if isinstance(caught_up, bool) and isinstance(fresh, bool):
                    res["initialblockdownload"] = not (caught_up and fresh)
        return out

    def _chain_name(self) -> str | None:
        """The node's network as normalize_chain names it; it never changes
        while the node runs, so it is read once."""
        if self._chain is None:
            info = self.backend.call("getblockchaininfo").get("result")
            chain = normalize_chain(info.get("chain")) if isinstance(info, dict) else None
            if chain in CASHADDR_PREFIX:
                self._chain = chain
        return self._chain

    def rpc_getblockheader(self, params: list[Any]) -> dict[str, Any]:
        verbose = True
        if len(params) > 1 and params[1] is not None:
            if not isinstance(params[1], bool):
                return self._err(-3, "JSON value is not a boolean as expected")
            verbose = params[1]
        out = self.backend.call("getblockheader", params[:1])
        error = out.get("error")
        if error:
            if isinstance(error, dict) and error.get("message") == "object does not exist":
                return self._err(-5, "Block not found")
            return out
        fields = out.get("result")
        if not isinstance(fields, dict):
            return out
        if not verbose:
            # kth ignores the verbose flag; serialize the header from its
            # fields and refuse to answer with bytes that are not the block.
            raw = header_from_fields(fields)
            if raw is None:
                return self._err(-32603, "Knuth returned an incomplete block header")
            if txid_of(raw) != decode_hash_param(params[0]):
                return self._err(-32603, "Knuth's header fields do not hash to the requested block")
            return self._ok(raw.hex())
        confirmations = self._header_confirmations(fields.get("hash"), fields.get("height"))
        if confirmations is None:
            return out
        header = {"hash": fields.get("hash"), "confirmations": confirmations}
        header.update((k, v) for k, v in fields.items() if k != "hash")
        return self._ok(header)

    def _header_confirmations(self, blockhash: Any, height: Any) -> int | None:
        """BCHN's confirmations: -1 off the active chain, else tip - height + 1."""
        if not isinstance(blockhash, str) or not isinstance(height, int):
            return None
        tip = self.backend.call("getblockcount").get("result")
        if not isinstance(tip, int):
            return None
        if height > tip:
            return -1
        active = self.backend.call("getblockhash", [height]).get("result")
        if not isinstance(active, str):
            return None
        return tip - height + 1 if active.lower() == blockhash.lower() else -1

    def rpc_getblockcount(self, params: list[Any]) -> dict[str, Any]:
        return self.backend.call("getblockcount", params)

    def rpc_getbestblockhash(self, params: list[Any]) -> dict[str, Any]:
        return self.backend.call("getbestblockhash", params)

    def rpc_getmininginfo(self, params: list[Any]) -> dict[str, Any]:
        out = self.backend.call("getmininginfo", params)
        res = out.get("result")
        if isinstance(res, dict) and res.get("chain") is not None:
            res["chain"] = normalize_chain(res["chain"])
        return out

    def rpc_getnetworkinfo(self, params: list[Any]) -> dict[str, Any]:
        native = self.backend.call("getnetworkinfo", params)
        if not native.get("error"):
            return native
        if native.get("error", {}).get("code") != -32601:
            return native
        peers = current_peer_count(self.debug_log)
        if peers is None:
            return self._err(-342, "Current peer count unavailable from Knuth RPC and status log")
        if self.version is None:
            return self._err(-342, "Installed Knuth version unavailable")
        major, minor, patch = (int(part) for part in self.version.split("."))
        info = self.backend.call("getblockchaininfo").get("result") or {}
        return self._ok(
            {
                "version": major * 100000 + minor * 10000 + patch * 100,
                "subversion": f"/Knuth:{self.version}/",
                "protocolversion": 70016,
                "connections": peers,
                "networks": [],
                "relayfee": 0.00001,
                "incrementalfee": 0.00001,
                "localaddresses": [],
                "warnings": "",
                "chain": normalize_chain(info.get("chain")),
            }
        )

    def rpc_getpeerinfo(self, params: list[Any]) -> dict[str, Any]:
        native = self.backend.call("getpeerinfo", params)
        if (native.get("error") or {}).get("code") == -32601:
            return self._err(-32601, "Knuth does not expose individual peer information")
        return native

    def rpc_help(self, params: list[Any]) -> dict[str, Any]:
        catalog = (
            "== Blockchain ==\n"
            "getbestblockhash\n"
            "getblock \"blockhash\" ( verbosity )\n"
            "getblockchaininfo\n"
            "getblockcount\n"
            "getblockhash height\n"
            "getblockheader \"blockhash\" ( verbose )\n"
            "getdifficulty\n"
            "\n== Rawtransactions ==\n"
            "getrawtransaction \"txid\" ( verbose )\n"
            "sendrawtransaction \"hexstring\"\n"
            "decoderawtransaction \"hexstring\"\n"
            "\n== Mining ==\n"
            "getblocktemplate ( template_request )\n"
            "getmininginfo\n"
            "submitblock \"hexdata\"\n"
            "getblocktemplatelight\n"
            "submitblocklight\n"
            "\n== Network ==\n"
            "getnetworkinfo\n"
            "getpeerinfo\n"
            "\n== Util ==\n"
            "validateaddress \"address\"\n"
            "estimatesmartfee conf_target\n"
            "estimatefee\n"
            "uptime\n"
            "getrawmempool ( verbose )\n"
            "getmempoolinfo\n"
            "getzmqnotifications\n"
            "getdsprooflist\n"
        )
        if params and isinstance(params[0], str):
            name = params[0]
            if name in catalog:
                return self._ok(name)
        return self._ok(catalog)

    def rpc_uptime(self, params: list[Any]) -> dict[str, Any]:
        return self._ok(int(time.time() - self.started))

    def rpc_getzmqnotifications(self, params: list[Any]) -> dict[str, Any]:
        return self._ok([])

    def rpc_getdsprooflist(self, params: list[Any]) -> dict[str, Any]:
        return self._ok([])

    def rpc_validateaddress(self, params: list[Any]) -> dict[str, Any]:
        # BCHN's validateaddress (rpc/misc.cpp): an invalid address answers
        # isvalid alone; a valid one its canonical CashAddr on this network,
        # its scriptPubKey, isscript and istokenaware.
        if len(params) != 1:
            return self._err(-1, 'validateaddress "address"')
        if not isinstance(params[0], str):
            return self._err(-3, "JSON value is not a string as expected")
        chain = self._chain_name()
        if chain is None:
            return self._err(-32603, "Cannot read the node's network from Knuth")
        decoded = decode_destination(params[0], chain)
        if decoded is None:
            return self._ok({"isvalid": False})
        is_script, hash_bytes, token_aware = decoded
        if not is_script:
            script = b"\x76\xa9\x14" + hash_bytes + b"\x88\xac"
        elif len(hash_bytes) == 20:
            script = b"\xa9\x14" + hash_bytes + b"\x87"
        else:
            script = b"\xaa\x20" + hash_bytes + b"\x87"
        kind = (1 if is_script else 0) + (2 if token_aware else 0)
        version = (kind << 3) | CASHADDR_HASH_SIZES.index(len(hash_bytes))
        return self._ok(
            {
                "isvalid": True,
                "address": encode_cashaddr(CASHADDR_PREFIX[chain], version, hash_bytes),
                "scriptPubKey": script.hex(),
                "isscript": is_script,
                "istokenaware": token_aware,
            }
        )

    def rpc_decoderawtransaction(self, params: list[Any]) -> dict[str, Any]:
        if not params or not isinstance(params[0], str):
            return self._err(-32602, "decoderawtransaction requires [hexstring]")
        try:
            raw = bytes.fromhex(params[0])
        except ValueError:
            return self._err(-22, "TX decode failed")
        try:
            obj = self._tx_verbose(raw, hex_rev(sha256d(raw)), "", None, None)
        except Exception as e:
            return self._err(-22, f"TX decode failed: {e}")
        return self._ok(obj)

    def rpc_estimatefee(self, params: list[Any]) -> dict[str, Any]:
        return self._ok(0.00001)

    def rpc_estimatesmartfee(self, params: list[Any]) -> dict[str, Any]:
        return self._ok({"feerate": 0.00001, "blocks": 2})

    def rpc_getblockhash(self, params: list[Any]) -> dict[str, Any]:
        if not params:
            return self._err(-32602, "getblockhash requires [height]")
        try:
            height = int(params[0])
        except (TypeError, ValueError):
            return self._err(-32602, "height must be an integer")
        out = self.backend.call("getblockhash", params)
        error = out.get("error")
        if isinstance(error, dict) and error.get("message") == "block height out of range":
            # BCHN's answer for a height past the tip (RPC_INVALID_PARAMETER).
            return self._err(-8, "Block height out of range")
        return out

    def rpc_sendrawtransaction(self, params: list[Any]) -> dict[str, Any]:
        out = self.backend.call("sendrawtransaction", params)
        if not params or not isinstance(params[0], str):
            return out
        try:
            raw = bytes.fromhex(params[0])
        except ValueError:
            return out
        txid = txid_of(raw)
        # Keep what kth pooled. Sending a pooled transaction again (kth
        # refuses it as a duplicate) also gives the proxy its bytes, which is
        # how one kth pooled before this proxy kept them can be supplied.
        if out.get("result") == txid or (
            out.get("error")
            and isinstance(self.backend.call("getmempoolentry", [txid]).get("result"), dict)
        ):
            self.txs.add(raw)
        return out

    def _confirmations(self, blockhash: str, height: Any) -> int | None:
        if not isinstance(height, int):
            return None
        active = self.backend.call("getblockhash", [height])
        if active.get("error") or not isinstance(active.get("result"), str):
            return None
        if active["result"].lower() != blockhash.lower():
            return -1
        tip = self.backend.call("getblockcount").get("result")
        return tip - height + 1 if isinstance(tip, int) and tip >= height else None

    def rpc_getblock(self, params: list[Any]) -> dict[str, Any]:
        if not params:
            return self._err(-32602, "getblock requires [blockhash]")
        # Bitcoin-RPC also accepts a height on some clients; Knuth does not.
        if isinstance(params[0], int) or (
            isinstance(params[0], str) and params[0].isdigit()
        ):
            hashed = self.rpc_getblockhash([int(params[0])])
            if hashed.get("error"):
                return hashed
            params = [hashed["result"], *params[1:]]
        blockhash = decode_hash_param(params[0])
        if not blockhash:
            return self._err(-32602, "invalid block hash")
        verbosity = parse_verbosity(params[1] if len(params) > 1 else 1)
        raw = self.index.read_block(blockhash)
        if raw is None:
            return self._err(-5, "Block not found")
        if verbosity == 0:
            return self._ok(raw.hex())
        header = self.backend.call("getblockheader", [blockhash]).get("result") or {}
        height = header.get("height")
        if height is None:
            height = self.index.height_of(blockhash)
        try:
            txs = iter_tx_slices(raw)
        except ValueError as e:
            return self._err(-32603, f"block parse failed: {e}")
        txids = [hex_rev(sha256d(tx)) for tx in txs]
        bits_hex = header.get("bits")
        if isinstance(bits_hex, str):
            try:
                bits_int = int(bits_hex, 16)
            except ValueError:
                bits_int = struct.unpack_from("<I", raw, 72)[0]
        else:
            bits_int = struct.unpack_from("<I", raw, 72)[0]
            bits_hex = f"{bits_int:08x}"
        confirmations = self._confirmations(blockhash, height)
        nxt = None
        if isinstance(confirmations, int) and confirmations > 1:
            nxt = self.backend.call("getblockhash", [height + 1]).get("result")
        obj: dict[str, Any] = {
            "hash": blockhash,
            "confirmations": confirmations,
            "strippedsize": len(raw),
            "size": len(raw),
            "weight": len(raw),
            "height": height,
            "version": header.get("version", struct.unpack_from("<I", raw, 0)[0]),
            "versionHex": f"{header.get('version', struct.unpack_from('<I', raw, 0)[0]):08x}",
            "merkleroot": header.get("merkleroot", hex_rev(raw[36:68])),
            "tx": txids,
            "time": header.get("time", struct.unpack_from("<I", raw, 68)[0]),
            "mediantime": header.get("mediantime", header.get("time")),
            "nonce": header.get("nonce", struct.unpack_from("<I", raw, 76)[0]),
            "bits": bits_hex,
            "difficulty": header.get("difficulty", bits_to_difficulty(bits_int)),
            "previousblockhash": header.get("previousblockhash", hex_rev(raw[4:36])),
            "nTx": len(txids),
        }
        if nxt:
            obj["nextblockhash"] = nxt
        if verbosity >= 2:
            obj["tx"] = []
            for rawtx, txid in zip(txs, txids):
                obj["tx"].append(self._tx_verbose(rawtx, txid, blockhash, height, confirmations))
        return self._ok(obj)

    def _tx_verbose(
        self,
        raw: bytes,
        txid: str,
        blockhash: str,
        height: Any,
        confirmations: Any,
    ) -> dict[str, Any]:
        i = 0
        version = struct.unpack_from("<I", raw, i)[0]
        i += 4
        nin, i = read_compact_size(raw, i)
        vin = []
        for _ in range(nin):
            prev = raw[i : i + 32]
            vout = struct.unpack_from("<I", raw, i + 32)[0]
            i += 36
            slen, i = read_compact_size(raw, i)
            script = raw[i : i + slen]
            i += slen
            seq = struct.unpack_from("<I", raw, i)[0]
            i += 4
            vin.append(
                {
                    "txid": hex_rev(prev),
                    "vout": vout,
                    "scriptSig": {"asm": "", "hex": script.hex()},
                    "sequence": seq,
                    "coinbase": script.hex() if prev == b"\x00" * 32 else None,
                }
            )
            if vin[-1]["coinbase"] is None:
                del vin[-1]["coinbase"]
        nout, i = read_compact_size(raw, i)
        vout_arr = []
        for n, _ in enumerate(range(nout)):
            value = struct.unpack_from("<Q", raw, i)[0]
            i += 8
            slen, i = read_compact_size(raw, i)
            script = raw[i : i + slen]
            i += slen
            vout_arr.append(
                {
                    "value": value / 1e8,
                    "n": n,
                    "scriptPubKey": {"asm": "", "hex": script.hex(), "type": "nonstandard"},
                }
            )
        locktime = struct.unpack_from("<I", raw, i)[0]
        return {
            "txid": txid,
            "hash": txid,
            "version": version,
            "size": len(raw),
            "locktime": locktime,
            "vin": vin,
            "vout": vout_arr,
            "hex": raw.hex(),
            "blockhash": blockhash,
            "confirmations": confirmations,
            "blocktime": None,
            "time": None,
        }

    def rpc_getrawtransaction(self, params: list[Any]) -> dict[str, Any]:
        if not params:
            return self._err(-32602, "getrawtransaction requires [txid]")
        txid = decode_hash_param(params[0])
        if not txid:
            return self._err(-32602, "invalid txid")
        verbose = bool(params[1]) if len(params) > 1 else False
        loc = self.index.txindex.get(txid)
        if not loc:
            self.index.ingest_new()
            loc = self.index.txindex.get(txid)
        if not loc:
            # mempool / unknown — try backend then fail cleanly (never dummy zeros)
            out = self.backend.call("getrawtransaction", params)
            res = out.get("result")
            if isinstance(res, str) and set(res) <= {"0"}:
                return self._err(-5, "No such mempool or blockchain transaction")
            if out.get("error"):
                return out
            return out
        blockhash, n = loc
        raw_block = self.index.read_block(blockhash)
        if raw_block is None:
            return self._err(-5, "No such mempool or blockchain transaction")
        txs = iter_tx_slices(raw_block)
        if n >= len(txs):
            return self._err(-5, "No such mempool or blockchain transaction")
        raw = txs[n]
        if not verbose:
            return self._ok(raw.hex())
        header = self.backend.call("getblockheader", [blockhash]).get("result") or {}
        height = header.get("height")
        confirmations = self._confirmations(blockhash, height)
        obj = self._tx_verbose(raw, txid, blockhash, height, confirmations)
        obj["blocktime"] = header.get("time")
        obj["time"] = header.get("time")
        return self._ok(obj)

    # ---- mining ------------------------------------------------------------

    def _tx_bytes(self, txid: str) -> bytes | None:
        """A transaction's bytes: relayed through this proxy, or in a block file."""
        raw = self.txs.get(txid)
        if raw is not None:
            return raw
        txindex = getattr(self.index, "txindex", None)
        if txindex is None:
            return None
        loc = txindex.get(txid)
        if loc is None:
            self.index.ingest_new()
            loc = txindex.get(txid)
        if loc is None:
            return None
        block = self.index.read_block(loc[0])
        try:
            txs = iter_tx_slices(block) if block is not None else []
        except ValueError:
            return None
        if loc[1] >= len(txs) or txid_of(txs[loc[1]]) != txid:
            return None
        return txs[loc[1]]

    def _mempool_entries(self, mempool: list[str]) -> dict[str, MempoolEntry]:
        """Fee, first-seen time and in-mempool parents of the pooled
        transactions; one that left the pool meanwhile is left out."""
        with self._cache_lock:
            entries = {txid: self._entries[txid] for txid in mempool if txid in self._entries}
        for txid in mempool:
            if txid in entries:
                continue
            entry = self.backend.call("getmempoolentry", [txid]).get("result")
            if not isinstance(entry, dict):
                continue
            fee, seen, depends = entry.get("fee"), entry.get("time"), entry.get("depends")
            if not isinstance(fee, int) or not isinstance(seen, int) or not isinstance(depends, list):
                continue
            entries[txid] = MempoolEntry(
                fee, seen, tuple(d.lower() for d in depends if isinstance(d, str))
            )
        pooled = set(mempool)
        with self._cache_lock:
            self._entries = entries
            self._sigchecks = {t: n for t, n in self._sigchecks.items() if t in pooled}
        return entries

    def _selection(
        self, template: dict[str, Any], entries: dict[str, MempoolEntry]
    ) -> list[str] | None:
        """The txids kth selected for `template`, in CTOR order, or None.

        kth names its selection only by job_id, a hash of the selected txids,
        and builds it from the pool as it was when the template was built (a
        template is reused for up to chain.gbt_template_refresh_seconds while
        the pool changes). It leaves out transactions that are not final at
        the template's height and those whose in-pool parents are left out.
        Candidates are the whole pool and the pool as first seen by the
        template's time; one is kth's selection only when its hash is the
        job_id, so a wrong candidate is never served.
        """
        job_id = template["job_id"]
        if job_id == EMPTY_SELECTION_JOB_ID:
            return []
        height, cutoff = template["height"], template["mintime"] - 1
        curtime = template["curtime"]
        nonfinal: set[str] = set()
        for txid in entries:
            raw = self._tx_bytes(txid)
            try:
                if raw is not None and not is_final(parse_transaction(raw), height, cutoff):
                    nonfinal.add(txid)
            except ValueError:
                continue
        pooled = set(entries)
        bases = (
            pooled,
            {txid for txid in pooled if entries[txid].time <= curtime},
            {txid for txid in pooled if entries[txid].time < curtime},
        )
        tried: set[tuple[str, ...]] = set()
        for base in bases:
            chosen = base - nonfinal
            changed = True
            while changed:
                orphans = {
                    txid for txid in chosen
                    if any(p in pooled and p not in chosen for p in entries[txid].depends)
                }
                chosen -= orphans
                changed = bool(orphans)
            candidate = tuple(sorted(chosen))
            if candidate in tried:
                continue
            tried.add(candidate)
            if selection_job_id(candidate) == job_id:
                return list(candidate)
        return None

    def _tx_sigchecks(self, txid: str, raw: bytes) -> int | None:
        with self._cache_lock:
            cached = self._sigchecks.get(txid)
        if cached is not None:
            return cached
        total = 0
        for txin in parse_transaction(raw).inputs:
            prev_raw = self._tx_bytes(hex_rev(txin.prev_hash))
            try:
                prev = parse_transaction(prev_raw) if prev_raw is not None else None
            except ValueError:
                prev = None
            if prev is None or txin.prev_index >= len(prev.outputs):
                return None
            locking = locking_bytecode(prev.outputs[txin.prev_index].script)
            checks = input_sigchecks(txin.script_sig, locking) if locking is not None else None
            if checks is None:
                return None
            total += checks
        with self._cache_lock:
            self._sigchecks[txid] = total
        return total

    def _full_template(self, template: dict[str, Any]) -> dict[str, Any] | None:
        """BCHN's getblocktemplate result for kth's light template, or None
        when kth's selection is not among the candidates (the pool moved).
        Raises TemplateUnavailable when it cannot be described exactly."""
        for key in ("job_id", "previousblockhash"):
            if not isinstance(template.get(key), str):
                raise TemplateUnavailable(-32603, f"Knuth's light template has no {key}")
        for key in ("height", "mintime", "curtime", "coinbasevalue"):
            if not isinstance(template.get(key), int):
                raise TemplateUnavailable(-32603, f"Knuth's light template has no {key}")
        entries: dict[str, MempoolEntry] = {}
        if template["job_id"] != EMPTY_SELECTION_JOB_ID:
            pool = self.backend.call("getrawmempool")
            mempool = pool.get("result")
            if pool.get("error") or not isinstance(mempool, list):
                error = pool.get("error") or {}
                raise TemplateUnavailable(
                    error.get("code", -32603), error.get("message", "Knuth's mempool is unreadable")
                )
            mempool = [txid.lower() for txid in mempool if isinstance(txid, str)]
            self.txs.retain(set(mempool))
            entries = self._mempool_entries(mempool)
        selection = self._selection(template, entries)
        if selection is None:
            return None
        transactions: list[dict[str, Any]] = []
        for txid in selection:
            raw = self._tx_bytes(txid)
            if raw is None:
                raise TemplateUnavailable(
                    -1,
                    f"Knuth's template includes transaction {txid}, whose bytes this proxy "
                    "does not have: Knuth returns no transaction bytes, so only transactions "
                    "sent through this RPC port or stored in block files are known. Sending "
                    "it again with sendrawtransaction through this port supplies them",
                )
            sigchecks = self._tx_sigchecks(txid, raw)
            if sigchecks is None:
                raise TemplateUnavailable(
                    -1,
                    f"Knuth's template includes transaction {txid}, whose SigChecks cannot be "
                    "read without running its scripts; Knuth does not report them",
                )
            transactions.append({
                "data": raw.hex(), "txid": txid, "hash": txid,
                "fee": entries[txid].fee, "sigops": sigchecks,
            })
        if sum(tx["fee"] for tx in transactions) > template["coinbasevalue"]:
            raise TemplateUnavailable(
                -32603, "Knuth's coinbase value is below the fees of its own template"
            )
        prev = template["previousblockhash"]
        return {
            "capabilities": ["proposal"],
            "version": template.get("version"),
            "previousblockhash": prev,
            "transactions": transactions,
            "coinbaseaux": {"flags": ""},
            "coinbasevalue": template["coinbasevalue"],
            "longpollid": f"{prev}{int(time.time())}",
            "target": template.get("target"),
            "mintime": template["mintime"],
            "mutable": ["time", "transactions", "prevblock"],
            "noncerange": "00000000ffffffff",
            "sigoplimit": template.get("sigchecklimit"),
            "sizelimit": template.get("sizelimit"),
            "curtime": template["curtime"],
            "bits": template.get("bits"),
            "height": template["height"],
        }

    def rpc_getblocktemplate(self, params: list[Any]) -> dict[str, Any]:
        request = params[0] if params else None
        if request is None:
            request = {}
        if not isinstance(request, dict):
            return self._err(-3, "JSON value is not an object as expected")
        mode = request.get("mode")
        if mode is not None and not isinstance(mode, str):
            return self._err(-8, "Invalid mode")
        if mode == "proposal":
            return self._proposal(request)
        if mode not in (None, "template"):
            return self._err(-8, "Invalid mode")
        for _ in range(TEMPLATE_ATTEMPTS):
            light = self.backend.call("getblocktemplatelight", [{}])
            if light.get("error") or not isinstance(light.get("result"), dict):
                return light
            try:
                gbt = self._full_template(light["result"])
            except TemplateUnavailable as e:
                return self._err(e.code, e.message)
            if gbt is not None:
                return self._ok(gbt)
        return self._err(
            -1, "Knuth's template does not match the transactions in its mempool; no template served"
        )

    def _known_block(self, block_hash: str) -> str | None:
        """BCHN's answer for a block the node already has: "duplicate" when it
        is on kth's active chain, "duplicate-inconclusive" when kth has its
        header elsewhere; None when kth does not know it."""
        header = self.backend.call("getblockheader", [block_hash]).get("result")
        if not isinstance(header, dict):
            return None
        height = header.get("height")
        if isinstance(height, int):
            active = self.backend.call("getblockhash", [height]).get("result")
            if isinstance(active, str) and active.lower() == block_hash:
                return "duplicate"
        return "duplicate-inconclusive"

    def _proposal(self, request: dict[str, Any]) -> dict[str, Any]:
        """getblocktemplate {"mode": "proposal"}: null for a block kth would
        take, else a BCHN reject reason. kth has no dry-run validation, so the
        header and coinbase are checked here and the other transactions must
        be exactly kth's current selection, which kth has validated."""
        data = request.get("data")
        if not isinstance(data, str):
            return self._err(-3, "Missing data String key for proposal")
        try:
            block = decode_block(bytes.fromhex(data))
        except ValueError:
            block = None
        if block is None:
            return self._err(-22, "Block decode failed")
        header, slices, txs = block
        known = self._known_block(txid_of(header))
        if known is not None:
            return self._ok(known)
        light = self.backend.call("getblocktemplatelight", [{}])
        template = light.get("result")
        if light.get("error") or not isinstance(template, dict):
            return light
        if hex_rev(header[4:36]) != str(template.get("previousblockhash", "")).lower():
            return self._ok("inconclusive-not-best-prevblk")
        return self._ok(proposal_reject_reason(header, slices, txs, template))

    def rpc_submitblock(self, params: list[Any]) -> dict[str, Any]:
        if not params or len(params) > 2:
            return self._err(-1, 'submitblock "hexdata" ( "dummy" )')
        if not isinstance(params[0], str):
            return self._err(-3, "JSON value is not a string as expected")
        try:
            block = decode_block(bytes.fromhex(params[0]))
        except ValueError:
            block = None
        if block is None:
            return self._err(-22, "Block decode failed")
        header, slices, txs = block
        if not txs or not txs[0].is_coinbase():
            return self._err(-22, "Block does not start with a coinbase")
        block_hash = txid_of(header)
        known = self._known_block(block_hash)
        if known is not None:
            return self._ok(known)
        # kth takes a block as its header and coinbase plus the job holding
        # the other transactions, and names jobs by their txids, so the job
        # of this block's transactions is known without having served it.
        job_id = selection_job_id(txid_of(tx) for tx in slices[1:])
        light_block = (header + write_compact_size(1) + slices[0]).hex()
        out = self.backend.call("submitblocklight", [light_block, job_id])
        if _job_missing(out):
            # kth keeps ten jobs for an hour; asking for its template again
            # registers the job anew while its selection is still this one.
            light = self.backend.call("getblocktemplatelight", [{}]).get("result")
            if isinstance(light, dict) and light.get("job_id") == job_id:
                out = self.backend.call("submitblocklight", [light_block, job_id])
        if _job_missing(out):
            return self._err(
                -1,
                "Knuth no longer holds the transactions of this block (its template expired "
                "or was replaced); the block was not submitted",
            )
        if out.get("result") in ("duplicate block", "block duplicate"):
            return self._ok(self._known_block(block_hash) or "duplicate-inconclusive")
        return out


def _job_missing(out: dict[str, Any]) -> bool:
    error = out.get("error")
    return (
        isinstance(error, dict)
        and error.get("code") == -32602
        and "job_id" in str(error.get("message", ""))
    )


def transaction_reject_reason(tx: Tx, size: int) -> str | None:
    """BCHN's CheckTransactionCommon."""
    if not tx.inputs:
        return "bad-txns-vin-empty"
    if not tx.outputs:
        return "bad-txns-vout-empty"
    if size > 1_000_000:
        return "bad-txns-oversize"
    total = 0
    for out in tx.outputs:
        if out.value < 0:
            return "bad-txns-vout-negative"
        if out.value > MAX_MONEY:
            return "bad-txns-vout-toolarge"
        total += out.value
        if total > MAX_MONEY:
            return "bad-txns-txouttotal-toolarge"
    return None


def proposal_reject_reason(
    header: bytes, slices: list[bytes], txs: list[Tx], template: dict[str, Any],
    now: float | None = None,
) -> str | None:
    """BCHN's reject reason for a block proposal on kth's `template`, in
    BCHN's order (ContextualCheckBlockHeader, CheckBlock, ContextualCheckBlock,
    ConnectBlock), or None. The transactions after the coinbase must be kth's
    selection: kth validated those, and only those can it take with a block."""
    now = time.time() if now is None else now
    version = struct.unpack_from("<i", header, 0)[0]
    timestamp, bits = struct.unpack_from("<II", header, 68)
    height, mintime = template.get("height"), template.get("mintime")
    if not isinstance(height, int) or not isinstance(mintime, int):
        return "inconclusive-not-best-prevblk"
    if f"{bits:08x}" != str(template.get("bits", "")).lower():
        return "bad-diffbits"
    if timestamp < mintime:
        return "time-too-old"
    if timestamp > now + MAX_FUTURE_BLOCK_TIME:
        return "time-too-new"
    if version < 4:
        return f"bad-version(0x{version & 0xFFFFFFFF:08x})"
    txids = [sha256d(tx) for tx in slices]
    if merkle_root(txids) != header[36:68]:
        return "bad-txnmrklroot"
    if not txs:
        return "bad-cb-missing"
    size = len(header) + len(write_compact_size(len(slices))) + sum(map(len, slices))
    if not isinstance(template.get("sizelimit"), int) or size > template["sizelimit"]:
        return "bad-blk-length"
    coinbase = txs[0]
    if not coinbase.is_coinbase():
        return "bad-cb-missing"
    reason = transaction_reject_reason(coinbase, len(slices[0]))
    if reason:
        return reason
    if not 2 <= len(coinbase.inputs[0].script_sig) <= MAX_COINBASE_SCRIPTSIG_SIZE:
        return "bad-cb-length"
    if any(tx.is_coinbase() for tx in txs[1:]):
        return "bad-tx-coinbase"
    if selection_job_id(hex_rev(txid) for txid in txids[1:]) != template.get("job_id"):
        return "inconclusive-txs-not-in-template"
    if not is_final(coinbase, height, mintime - 1):
        return "bad-txns-nonfinal"
    if len(slices[0]) < MIN_TX_SIZE:
        return "bad-txns-undersize"
    if coinbase.version not in TX_VERSIONS:
        return "bad-txns-version"
    if not coinbase.inputs[0].script_sig.startswith(coinbase_height_prefix(height)):
        return "bad-cb-height"
    coinbase_value = template.get("coinbasevalue")
    if not isinstance(coinbase_value, int) or sum(o.value for o in coinbase.outputs) > coinbase_value:
        return "bad-cb-amount"
    return None


def parse_ini(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("[") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _read_headers(
    sock: socket.socket, leftover: bytes = b"", limit: int = 65536
) -> tuple[bytes, bytes, bytes]:
    """Return (head, body, leftover). leftover keeps pipelined next request."""
    buf = leftover
    sock.settimeout(20)
    while b"\r\n\r\n" not in buf and b"\n\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            break
    if b"\r\n\r\n" in buf:
        head, rest = buf.split(b"\r\n\r\n", 1)
    elif b"\n\n" in buf:
        head, rest = buf.split(b"\n\n", 1)
    else:
        return buf, b"", b""
    headers: dict[str, str] = {}
    for line in head.replace(b"\r\n", b"\n").split(b"\n")[1:]:
        if b":" not in line:
            continue
        k, v = line.split(b":", 1)
        headers[k.decode("latin1").strip().lower()] = v.decode("latin1").strip()
    length = int(headers.get("content-length") or 0)
    while len(rest) < length:
        chunk = sock.recv(length - len(rest))
        if not chunk:
            break
        rest += chunk
    body = rest[:length] if length else b""
    extra = rest[length:] if length else b""
    return head, body, extra


def make_handler(compat: Compat, user: str, password: str):
    expect = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            sock: socket.socket = self.request
            leftover = b""
            # Fulcrum keeps the HTTP/1.1 socket open and pipelines RPC calls.
            # leftover preserves the next pipelined request (uptime after getblock).
            while True:
                try:
                    head, raw, leftover = _read_headers(sock, leftover)
                except Exception as e:
                    sys.stderr.write(f"{self.client_address[0]} read error {e}\n")
                    return
                if not head:
                    return
                first = head.split(b"\n", 1)[0].decode("latin1", "replace")
                headers = {}
                for line in head.replace(b"\r\n", b"\n").split(b"\n")[1:]:
                    if b":" not in line:
                        continue
                    k, v = line.split(b":", 1)
                    headers[k.decode("latin1").strip().lower()] = v.decode("latin1").strip()
                conn = headers.get("connection", "").lower()
                auth = headers.get("authorization", "")
                if auth != expect:
                    payload = b'{"result":null,"error":{"code":-18,"message":"unauthorized"},"id":null}'
                    self._reply(401, payload, extra='WWW-Authenticate: Basic realm="jsonrpc"\r\n')
                    return
                try:
                    body = json.loads(raw.decode() or "null")
                except json.JSONDecodeError:
                    payload = b'{"result":null,"error":{"code":-32700,"message":"parse error"},"id":null}'
                    self._reply(200, payload)
                    return
                reqs = body if isinstance(body, list) else [body]
                replies = []
                methods: list[str] = []
                for req in reqs:
                    if not isinstance(req, dict):
                        replies.append(
                            {"result": None, "error": {"code": -32600, "message": "invalid request"}, "id": None}
                        )
                        continue
                    method = req.get("method")
                    params = req.get("params") or []
                    if not isinstance(params, list):
                        params = [params]
                    rid = req.get("id")
                    if not isinstance(method, str):
                        replies.append(
                            {"result": None, "error": {"code": -32600, "message": "invalid request"}, "id": rid}
                        )
                        continue
                    methods.append(method)
                    out = compat.handle(method, params)
                    out["id"] = rid
                    replies.append(out)
                # Trailing LF is required: ckpool/EloPool read_socket_line()
                # waits for '\n' after HTTP/1.1 200 OK and never uses
                # Content-Length. Without it, GBT sits 20s then
                # "No bitcoinds active". ASICSeer reads Content-Length and
                # json_loads ignores the extra newline.
                payload = json.dumps(
                    replies if isinstance(body, list) else replies[0],
                    separators=(",", ":"),
                ).encode() + b"\n"
                sys.stderr.write(
                    f"{self.client_address[0]} {first!r} methods={methods} bytes={len(payload)}\n"
                )
                self._reply(200, payload)
                if conn == "close" or first.startswith("POST / HTTP/1.0"):
                    return

        def _reply(self, code: int, payload: bytes, extra: str = "") -> None:
            reason = {200: "OK", 401: "Unauthorized"}.get(code, "OK")
            # ckpool/asicseer parse "HTTP/1.1 200 OK" and "Content-Length: "
            # using LF (not CRLF) and a 60s line timeout.
            # CRLF is required: ckpool/asicseer split on '\n', so the blank
            # line after Content-Length must be '\r' (ret==1). LF-only
            # gives a zero-length line and they abort. Content-Length last.
            extra_crlf = extra.replace("\n", "\r\n") if extra else ""
            hdr = (
                f"HTTP/1.1 {code} {reason}\r\n"
                f"Content-Type: application/json\r\n"
                f"{extra_crlf}"
                f"Content-Length: {len(payload)}\r\n"
                f"\r\n"
            )
            try:
                self.request.sendall(hdr.encode("latin1") + payload)
            except OSError:
                pass

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="Knuth Bitcoin-RPC compatibility sidecar")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=48332)
    ap.add_argument("--backend", default="http://127.0.0.1:19332/")
    ap.add_argument("--blocks", default="/data/chipnet/blocks")
    ap.add_argument("--config", default="/data/kth.cfg")
    ap.add_argument("--store", default="/data/store.json")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--binary", default="/usr/local/bin/kth")
    ap.add_argument("--debug-log", default="/data/debug.log")
    args = ap.parse_args()

    cfg = parse_ini(Path(args.config))
    user = args.user or cfg.get("rpc.user") or "knuth"
    password = args.password or cfg.get("rpc.password") or ""
    if not password:
        store = Path(args.store)
        if store.exists():
            data = json.loads(store.read_text())
            user = data.get("rpcUser") or user
            password = data.get("rpcPassword") or password
    if not password:
        print("[rpc-compat] missing RPC password", file=sys.stderr)
        return 2

    blocks_dir = Path(args.blocks)
    if not blocks_dir.is_dir():
        # Derive from cfg db.directory when possible.
        db = cfg.get("db.directory")
        if db:
            cand = Path(db) / "blocks"
            if cand.is_dir():
                blocks_dir = cand
    if not blocks_dir.is_dir():
        print(f"[rpc-compat] blocks dir missing: {blocks_dir}", file=sys.stderr)
        return 2

    index = BlockIndex(blocks_dir)
    index.rebuild()
    index.start_watcher()
    backend = Knuth(args.backend if args.backend.endswith("/") else args.backend + "/", user, password)
    relayed = TxStore(blocks_dir.parent / "rpc-compat-mempool.txt")
    compat = Compat(backend, index, Path(args.debug_log), node_version(args.binary), relayed)

    class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True
        request_queue_size = 128

    server = Server((args.bind, args.port), make_handler(compat, user, password))
    print(f"[rpc-compat] listening on {args.bind}:{args.port} -> {args.backend}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
