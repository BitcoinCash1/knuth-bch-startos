#!/usr/bin/env python3
"""Bitcoin-RPC compatibility sidecar for Knuth v1.3.0.

kth 1.3.0 documents getblock/getrawtransaction, but fetch_block() is a stub
after the LMDB→blk*.dat move (block_chain.cpp: "return not_found until we
implement reading from flat files"). It also has no getnetworkinfo,
getblocktemplate, or submitblock — which Fulcrum, BCH Explorer, ASICSeer and
EloPool all need.

This process sits in front of kth, serves those methods from the flat block
files + light-GBT, and proxies everything else unchanged.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import threading
import time
import socket
import socketserver
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ZERO_HASH = "00" * 32
CASHADDR_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CASHADDR_GEN = [0x98F2BC8E61, 0x79B76D99E2, 0xF33E5FB3C4, 0xAE2EABE2A8, 0x1E4F43E470]


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


def decode_cashaddr(addr: str) -> tuple[str, int, bytes] | None:
    """Return (prefix, type, payload-bytes) or None if the cashaddr is invalid."""
    try:
        lower = addr.lower()
        if ":" not in lower:
            return None
        prefix, payload = lower.split(":", 1)
        if prefix not in {"bitcoincash", "bchtest", "bchreg", "bchswitch"}:
            return None
        if any(c not in CASHADDR_CHARSET for c in payload):
            return None
        data = [CASHADDR_CHARSET.index(c) for c in payload]
        pref = [ord(c) & 31 for c in prefix] + [0]
        # Valid cashaddrs yield 0 or 1 depending on encoder XOR convention.
        if _cashaddr_polymod(pref + data) not in (0, 1):
            return None
        if len(data) < 8 + 1:
            return None
        payload8 = _convertbits(data[:-8], 5, 8, pad=False)
        if payload8 is None or not payload8:
            return None
        version = payload8[0]
        kind = version >> 3
        return prefix, kind, bytes(payload8[1:])
    except Exception:
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


class Compat:
    def __init__(self, backend: Knuth, index: BlockIndex):
        self.backend = backend
        self.index = index
        self.started = time.time()
        self.jobs: dict[str, str] = {}
        self._job_lock = threading.Lock()

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
            # kth's RPC `blocks` lags the files / headers. Prefer the index tip
            # so Fulcrum does not rewind a healthy indexer, and so health does
            # not sit on "Syncing 100%" (blocks=N-5, headers=N).
            tip_h = int(self.index.tip_height)
            if tip_h > int(res.get("blocks") or 0):
                res["blocks"] = tip_h
                res["bestblockhash"] = self.index.tip_hash
            hdr = int(res.get("headers") or 0)
            if tip_h > hdr:
                res["headers"] = tip_h
            elif hdr < int(res.get("blocks") or 0):
                res["headers"] = int(res["blocks"])
        return out

    def rpc_getblockcount(self, params: list[Any]) -> dict[str, Any]:
        out = self.backend.call("getblockcount", params)
        n = out.get("result")
        if isinstance(n, int) and self.index.tip_height > n:
            return self._ok(self.index.tip_height)
        if self.index.tip_height >= 0 and (n is None or out.get("error")):
            return self._ok(self.index.tip_height)
        return out

    def rpc_getbestblockhash(self, params: list[Any]) -> dict[str, Any]:
        if self.index.tip_hash and self.index.tip_hash != ZERO_HASH:
            return self._ok(self.index.tip_hash)
        return self.backend.call("getbestblockhash", params)

    def rpc_getmininginfo(self, params: list[Any]) -> dict[str, Any]:
        out = self.backend.call("getmininginfo", params)
        res = out.get("result")
        if isinstance(res, dict) and res.get("chain") is not None:
            res["chain"] = normalize_chain(res["chain"])
        return out

    def rpc_getnetworkinfo(self, params: list[Any]) -> dict[str, Any]:
        info = self.backend.call("getblockchaininfo").get("result") or {}
        return self._ok(
            {
                "version": 130000,
                "subversion": "/Knuth:1.3.0/",
                "protocolversion": 70016,
                "localservices": "0000000000000409",
                "localservicesnames": ["NETWORK", "NETWORK_LIMITED"],
                "localrelay": True,
                "timeoffset": 0,
                "networkactive": True,
                "connections": 8,
                "connections_in": 0,
                "connections_out": 8,
                "networks": [
                    {
                        "name": "ipv4",
                        "limited": False,
                        "reachable": True,
                        "proxy": "",
                        "proxy_randomize_credentials": False,
                    }
                ],
                "relayfee": 0.00001,
                "incrementalfee": 0.00001,
                "localaddresses": [],
                "warnings": "",
                "chain": normalize_chain(info.get("chain")),
            }
        )

    def rpc_getpeerinfo(self, params: list[Any]) -> dict[str, Any]:
        return self._ok([])

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
        if not params or not isinstance(params[0], str):
            return self._err(-32602, "validateaddress requires [address]")
        addr = params[0].strip()
        parsed = decode_cashaddr(addr)
        if parsed is None:
            return self._ok({"isvalid": False})
        prefix, kind, payload = parsed
        if kind in (0, 2) and len(payload) == 20:
            script = "76a914" + payload.hex() + "88ac"
        elif kind in (1, 3) and len(payload) == 20:
            script = "a914" + payload.hex() + "87"
        else:
            script = payload.hex()
        return self._ok(
            {
                "isvalid": True,
                "address": addr,
                "scriptPubKey": script,
                "isscript": kind in (1, 3),
                "iswitness": False,
                "istokenaware": kind in (2, 3),
                "prefix": prefix,
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
        h = self.index.hash_at(height)
        if h:
            return self._ok(h)
        return self.backend.call("getblockhash", params)

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
        tip = self.backend.call("getblockcount").get("result")
        confirmations = None
        if isinstance(tip, int) and isinstance(height, int):
            confirmations = tip - height + 1
        nxt = None
        if isinstance(height, int):
            nxt = self.index.hash_at(height + 1)
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
        tip = self.backend.call("getblockcount").get("result")
        confirmations = None
        if isinstance(tip, int) and isinstance(height, int):
            confirmations = tip - height + 1
        obj = self._tx_verbose(raw, txid, blockhash, height, confirmations)
        obj["blocktime"] = header.get("time")
        obj["time"] = header.get("time")
        return self._ok(obj)

    def rpc_getblocktemplate(self, params: list[Any]) -> dict[str, Any]:
        light = self.backend.call("getblocktemplatelight", [{}])
        if light.get("error") or not isinstance(light.get("result"), dict):
            return light
        t = light["result"]
        job_id = t.get("job_id")
        prev = t.get("previousblockhash")
        # Prefer the indexed tip when kth's RPC tip is stale.
        if self.index.tip_hash != ZERO_HASH:
            prev = self.index.tip_hash
        if isinstance(job_id, str) and isinstance(prev, str):
            with self._job_lock:
                self.jobs[prev.lower()] = job_id
        txs: list[dict[str, Any]] = []
        mempool = self.backend.call("getrawmempool").get("result") or []
        if isinstance(mempool, list):
            for txid in mempool:
                raw = self.backend.call("getrawtransaction", [txid])
                hexdata = raw.get("result")
                if not isinstance(hexdata, str) or set(hexdata) <= {"0"}:
                    continue
                txs.append({"data": hexdata, "txid": txid, "hash": txid})
        bits = t.get("bits")
        target = t.get("target")
        height = t.get("height")
        if isinstance(prev, str):
            hdr = self.backend.call("getblockheader", [prev]).get("result") or {}
            prev_bits = hdr.get("bits")
            if (
                isinstance(prev_bits, str)
                and bits in (None, "1d00ffff", "1D00FFFF")
                and prev_bits.lower() != "1d00ffff"
            ):
                bits = prev_bits
                try:
                    target = f"{bits_to_target(int(prev_bits, 16)):064x}"
                except ValueError:
                    pass
            if self.index.tip_height >= 0:
                height = self.index.tip_height + 1
        gbt = {
            "capabilities": ["proposal"],
            "version": t.get("version"),
            "previousblockhash": prev,
            "transactions": txs,
            "coinbaseaux": {"flags": ""},
            "coinbasevalue": t.get("coinbasevalue"),
            "longpollid": f"{prev}{int(time.time())}",
            "target": target,
            "mintime": t.get("mintime"),
            "mutable": t.get("mutable") or ["time", "transactions", "prevblock"],
            "noncerange": t.get("noncerange") or "00000000ffffffff",
            "sigoplimit": t.get("sigchecklimit"),
            "sizelimit": t.get("sizelimit"),
            "curtime": t.get("curtime"),
            "bits": bits,
            "height": height,
        }
        return self._ok(gbt)

    def rpc_submitblock(self, params: list[Any]) -> dict[str, Any]:
        if not params or not isinstance(params[0], str):
            return self._err(-32602, "submitblock requires [hexdata]")
        try:
            raw = bytes.fromhex(params[0])
        except ValueError:
            return self._err(-32602, "hexdata is not valid hex")
        if len(raw) < 80:
            return self._err(-32602, "block too short")
        prev = hex_rev(raw[4:36])
        with self._job_lock:
            job_id = self.jobs.get(prev.lower())
        if not job_id:
            light = self.backend.call("getblocktemplatelight", [{}])
            tres = light.get("result") or {}
            job_id = tres.get("job_id")
        if not job_id:
            return self._err(-32603, "no light-GBT job to attach submitblock to")
        try:
            txs = iter_tx_slices(raw)
        except ValueError as e:
            return self._err(-32602, f"block parse failed: {e}")
        if not txs:
            return self._err(-32602, "block has no coinbase")
        light_block = raw[:80] + write_compact_size(1) + txs[0]
        return self.backend.call("submitblocklight", [light_block.hex(), job_id])


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
    compat = Compat(backend, index)

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
